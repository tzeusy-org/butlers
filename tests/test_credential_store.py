"""Unit tests for butlers.credential_store.CredentialStore.

All tests mock the asyncpg pool — no real database required.

Coverage:
- store()            — insert / upsert, validation, no-value-in-logs
- load()             — DB hit / miss
- resolve()          — DB-first + env fallback + env_fallback=False
- has()              — DB hit / miss
- delete()           — row deleted / not found
- list_secrets()     — all / by category, metadata only
- SecretMetadata     — repr never exposes raw values
- CredentialStore    — repr is safe
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
    backfill_shared_secrets,
    legacy_shared_db_name_from_env,
    shared_db_name_from_env,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


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
    # Stash conn for easy assertion access
    pool._conn = conn
    return pool


def _make_row(**kwargs) -> MagicMock:
    """Build a mock asyncpg Record-like object."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: kwargs[key]
    return row


# ---------------------------------------------------------------------------
# store()
# ---------------------------------------------------------------------------


class TestStore:
    async def test_store_executes_upsert(self) -> None:
        pool = _make_pool(execute_return="INSERT 0 1")
        store = CredentialStore(pool)
        await store.store("MY_KEY", "my-value")
        assert pool._conn.execute.call_count == 1
        sql, *args = pool._conn.execute.call_args[0]
        assert "INSERT INTO" in sql
        assert "butler_secrets" in sql
        assert "ON CONFLICT" in sql
        assert args[0] == "MY_KEY"
        assert args[1] == "my-value"

    async def test_store_uses_category_default(self) -> None:
        pool = _make_pool()
        store = CredentialStore(pool)
        await store.store("KEY", "value")
        _, *args = pool._conn.execute.call_args[0]
        category_arg = args[2]
        assert category_arg == "general"

    async def test_store_passes_custom_category(self) -> None:
        pool = _make_pool()
        store = CredentialStore(pool)
        await store.store("KEY", "value", category="telegram")
        _, *args = pool._conn.execute.call_args[0]
        assert args[2] == "telegram"

    async def test_store_passes_description(self) -> None:
        pool = _make_pool()
        store = CredentialStore(pool)
        await store.store("KEY", "value", description="My secret description")
        _, *args = pool._conn.execute.call_args[0]
        assert args[3] == "My secret description"

    async def test_store_is_sensitive_defaults_true(self) -> None:
        pool = _make_pool()
        store = CredentialStore(pool)
        await store.store("KEY", "value")
        _, *args = pool._conn.execute.call_args[0]
        assert args[4] is True

    async def test_store_is_sensitive_false(self) -> None:
        pool = _make_pool()
        store = CredentialStore(pool)
        await store.store("KEY", "value", is_sensitive=False)
        _, *args = pool._conn.execute.call_args[0]
        assert args[4] is False

    async def test_store_passes_expires_at(self) -> None:
        pool = _make_pool()
        store = CredentialStore(pool)
        expiry = _NOW
        await store.store("KEY", "value", expires_at=expiry)
        _, *args = pool._conn.execute.call_args[0]
        assert args[5] == expiry

    async def test_store_strips_key_whitespace(self) -> None:
        pool = _make_pool()
        store = CredentialStore(pool)
        await store.store("  KEY  ", "value")
        _, *args = pool._conn.execute.call_args[0]
        assert args[0] == "KEY"

    async def test_store_empty_key_raises(self) -> None:
        pool = _make_pool()
        store = CredentialStore(pool)
        with pytest.raises(ValueError, match="key"):
            await store.store("", "value")

    async def test_store_whitespace_only_key_raises(self) -> None:
        pool = _make_pool()
        store = CredentialStore(pool)
        with pytest.raises(ValueError, match="key"):
            await store.store("   ", "value")

    async def test_store_empty_value_raises(self) -> None:
        pool = _make_pool()
        store = CredentialStore(pool)
        with pytest.raises(ValueError, match="value"):
            await store.store("KEY", "")

    async def test_store_does_not_log_value(self, caplog: pytest.LogCaptureFixture) -> None:
        pool = _make_pool()
        store = CredentialStore(pool)
        with caplog.at_level("DEBUG"):
            await store.store("MY_KEY", "SUPER_SECRET_VALUE_XYZ")
        for record in caplog.records:
            assert "SUPER_SECRET_VALUE_XYZ" not in record.getMessage()


# ---------------------------------------------------------------------------
# load()
# ---------------------------------------------------------------------------


class TestLoad:
    async def test_load_returns_value_when_found(self) -> None:
        row = _make_row(secret_value="tok_abc123")
        pool = _make_pool(fetchrow_return=row)
        store = CredentialStore(pool)
        result = await store.load("MY_KEY")
        assert result == "tok_abc123"

    async def test_load_returns_none_when_not_found(self) -> None:
        pool = _make_pool(fetchrow_return=None)
        store = CredentialStore(pool)
        result = await store.load("MISSING_KEY")
        assert result is None

    async def test_load_queries_correct_table(self) -> None:
        pool = _make_pool(fetchrow_return=None)
        store = CredentialStore(pool)
        await store.load("MY_KEY")
        sql, *args = pool._conn.fetchrow.call_args[0]
        assert "butler_secrets" in sql
        assert args[0] == "MY_KEY"


# ---------------------------------------------------------------------------
# resolve()
# ---------------------------------------------------------------------------


class TestResolve:
    async def test_resolve_returns_db_value_when_present(self) -> None:
        row = _make_row(secret_value="db-secret-value")
        pool = _make_pool(fetchrow_return=row)
        store = CredentialStore(pool)
        result = await store.resolve("MY_KEY")
        assert result == "db-secret-value"

    async def test_resolve_falls_back_to_env_when_db_miss(self) -> None:
        pool = _make_pool(fetchrow_return=None)
        store = CredentialStore(pool)
        with patch.dict(os.environ, {"MY_KEY": "env-secret-value"}):
            result = await store.resolve("MY_KEY")
        assert result == "env-secret-value"

    async def test_resolve_returns_none_when_neither_db_nor_env(self) -> None:
        pool = _make_pool(fetchrow_return=None)
        store = CredentialStore(pool)
        env = {k: v for k, v in os.environ.items() if k != "MY_KEY"}
        with patch.dict(os.environ, env, clear=True):
            result = await store.resolve("MY_KEY")
        assert result is None

    async def test_resolve_db_wins_over_env(self) -> None:
        row = _make_row(secret_value="db-value")
        pool = _make_pool(fetchrow_return=row)
        store = CredentialStore(pool)
        with patch.dict(os.environ, {"MY_KEY": "env-value"}):
            result = await store.resolve("MY_KEY")
        assert result == "db-value"

    async def test_resolve_env_fallback_false_skips_env(self) -> None:
        pool = _make_pool(fetchrow_return=None)
        store = CredentialStore(pool)
        with patch.dict(os.environ, {"MY_KEY": "env-value"}):
            result = await store.resolve("MY_KEY", env_fallback=False)
        assert result is None

    async def test_resolve_empty_env_value_is_ignored(self) -> None:
        pool = _make_pool(fetchrow_return=None)
        store = CredentialStore(pool)
        with patch.dict(os.environ, {"MY_KEY": ""}):
            result = await store.resolve("MY_KEY")
        assert result is None

    async def test_resolve_uses_fallback_pool_when_local_missing(self) -> None:
        local_pool = _make_pool(fetchrow_return=None)
        fallback_row = _make_row(secret_value="shared-secret")
        fallback_pool = _make_pool(fetchrow_return=fallback_row)
        store = CredentialStore(local_pool, fallback_pools=[fallback_pool])

        result = await store.resolve("MY_KEY", env_fallback=False)

        assert result == "shared-secret"
        assert local_pool.acquire.call_count == 1
        assert fallback_pool.acquire.call_count == 1

    async def test_local_value_overrides_fallback_value(self) -> None:
        local_row = _make_row(secret_value="local-secret")
        local_pool = _make_pool(fetchrow_return=local_row)
        fallback_row = _make_row(secret_value="shared-secret")
        fallback_pool = _make_pool(fetchrow_return=fallback_row)
        store = CredentialStore(local_pool, fallback_pools=[fallback_pool])

        result = await store.resolve("MY_KEY", env_fallback=False)

        assert result == "local-secret"
        # No fallback hit because local already resolved
        assert fallback_pool.acquire.call_count == 0

    async def test_resolve_uses_second_fallback_before_env(self) -> None:
        local_pool = _make_pool(fetchrow_return=None)
        shared_pool = _make_pool(fetchrow_return=None)
        legacy_row = _make_row(secret_value="legacy-secret")
        legacy_pool = _make_pool(fetchrow_return=legacy_row)
        store = CredentialStore(local_pool, fallback_pools=[shared_pool, legacy_pool])

        with patch.dict(os.environ, {"MY_KEY": "env-secret"}):
            result = await store.resolve("MY_KEY")

        assert result == "legacy-secret"
        assert local_pool.acquire.call_count == 1
        assert shared_pool.acquire.call_count == 1
        assert legacy_pool.acquire.call_count == 1


# ---------------------------------------------------------------------------
# has()
# ---------------------------------------------------------------------------


class TestHas:
    async def test_has_returns_true_when_row_exists(self) -> None:
        row = _make_row()
        pool = _make_pool(fetchrow_return=row)
        store = CredentialStore(pool)
        result = await store.has("MY_KEY")
        assert result is True

    async def test_has_returns_false_when_row_missing(self) -> None:
        pool = _make_pool(fetchrow_return=None)
        store = CredentialStore(pool)
        result = await store.has("MY_KEY")
        assert result is False

    async def test_has_queries_correct_table(self) -> None:
        pool = _make_pool(fetchrow_return=None)
        store = CredentialStore(pool)
        await store.has("MY_KEY")
        sql, *args = pool._conn.fetchrow.call_args[0]
        assert "butler_secrets" in sql
        assert args[0] == "MY_KEY"


# ---------------------------------------------------------------------------
# delete()
# ---------------------------------------------------------------------------


class TestDelete:
    async def test_delete_returns_true_when_row_deleted(self) -> None:
        pool = _make_pool(execute_return="DELETE 1")
        store = CredentialStore(pool)
        result = await store.delete("MY_KEY")
        assert result is True

    async def test_delete_returns_false_when_not_found(self) -> None:
        pool = _make_pool(execute_return="DELETE 0")
        store = CredentialStore(pool)
        result = await store.delete("MISSING_KEY")
        assert result is False

    async def test_delete_executes_correct_sql(self) -> None:
        pool = _make_pool(execute_return="DELETE 1")
        store = CredentialStore(pool)
        await store.delete("MY_KEY")
        sql, *args = pool._conn.execute.call_args[0]
        assert "DELETE FROM" in sql
        assert "butler_secrets" in sql
        assert args[0] == "MY_KEY"


# ---------------------------------------------------------------------------
# list_secrets()
# ---------------------------------------------------------------------------


class TestListSecrets:
    def _make_db_row(
        self,
        key: str,
        *,
        category: str = "general",
        description: str | None = None,
        is_sensitive: bool = True,
    ) -> MagicMock:
        row = MagicMock()
        row.__getitem__ = lambda self, k: {
            "secret_key": key,
            "category": category,
            "description": description,
            "is_sensitive": is_sensitive,
            "created_at": _NOW,
            "updated_at": _NOW,
            "expires_at": None,
        }[k]
        return row

    async def test_list_returns_empty_when_no_secrets(self) -> None:
        pool = _make_pool(fetch_return=[])
        store = CredentialStore(pool)
        result = await store.list_secrets()
        assert result == []

    async def test_list_returns_metadata_objects(self) -> None:
        rows = [self._make_db_row("K1"), self._make_db_row("K2", category="telegram")]
        pool = _make_pool(fetch_return=rows)
        store = CredentialStore(pool)
        result = await store.list_secrets()
        assert len(result) == 2
        assert all(isinstance(m, SecretMetadata) for m in result)

    async def test_list_sets_is_set_true_for_each_row(self) -> None:
        rows = [self._make_db_row("K1")]
        pool = _make_pool(fetch_return=rows)
        store = CredentialStore(pool)
        result = await store.list_secrets()
        assert result[0].is_set is True

    async def test_list_sets_source_database(self) -> None:
        rows = [self._make_db_row("K1")]
        pool = _make_pool(fetch_return=rows)
        store = CredentialStore(pool)
        result = await store.list_secrets()
        assert result[0].source == "database"

    async def test_list_does_not_include_raw_values(self) -> None:
        """SecretMetadata has no attribute for the raw value."""
        rows = [self._make_db_row("K1")]
        pool = _make_pool(fetch_return=rows)
        store = CredentialStore(pool)
        result = await store.list_secrets()
        meta = result[0]
        assert not hasattr(meta, "secret_value")
        assert not hasattr(meta, "value")

    async def test_list_filters_by_category(self) -> None:
        pool = _make_pool(fetch_return=[])
        store = CredentialStore(pool)
        await store.list_secrets(category="telegram")
        sql, *args = pool._conn.fetch.call_args[0]
        assert "category" in sql
        assert args[0] == "telegram"

    async def test_list_without_category_uses_no_filter(self) -> None:
        pool = _make_pool(fetch_return=[])
        store = CredentialStore(pool)
        await store.list_secrets()
        sql, *_args = pool._conn.fetch.call_args[0]
        # No WHERE clause binding when listing all
        assert "WHERE" not in sql

    async def test_list_populates_description(self) -> None:
        rows = [self._make_db_row("K1", description="My description")]
        pool = _make_pool(fetch_return=rows)
        store = CredentialStore(pool)
        result = await store.list_secrets()
        assert result[0].description == "My description"

    async def test_list_populates_is_sensitive_false(self) -> None:
        rows = [self._make_db_row("K1", is_sensitive=False)]
        pool = _make_pool(fetch_return=rows)
        store = CredentialStore(pool)
        result = await store.list_secrets()
        assert result[0].is_sensitive is False

    async def test_list_handles_expires_at_none(self) -> None:
        rows = [self._make_db_row("K1")]
        pool = _make_pool(fetch_return=rows)
        store = CredentialStore(pool)
        result = await store.list_secrets()
        assert result[0].expires_at is None

    async def test_list_handles_expires_at_set(self) -> None:
        row = MagicMock()
        expiry = _NOW
        row.__getitem__ = lambda self, k: {
            "secret_key": "K1",
            "category": "general",
            "description": None,
            "is_sensitive": True,
            "created_at": _NOW,
            "updated_at": _NOW,
            "expires_at": expiry,
        }[k]
        pool = _make_pool(fetch_return=[row])
        store = CredentialStore(pool)
        result = await store.list_secrets()
        assert result[0].expires_at == expiry


# ---------------------------------------------------------------------------
# SecretMetadata repr — never exposes raw values
# ---------------------------------------------------------------------------


class TestSecretMetadataRepr:
    def _make_meta(self, key: str = "MY_KEY") -> SecretMetadata:
        return SecretMetadata(
            key=key,
            category="general",
            description=None,
            is_sensitive=True,
            is_set=True,
            created_at=_NOW,
            updated_at=_NOW,
            expires_at=None,
            source="database",
        )

    def test_repr_includes_key(self) -> None:
        meta = self._make_meta("MY_KEY")
        assert "MY_KEY" in repr(meta)

    def test_repr_includes_category(self) -> None:
        meta = self._make_meta()
        assert "general" in repr(meta)

    def test_repr_includes_is_set(self) -> None:
        meta = self._make_meta()
        assert "is_set=" in repr(meta)

    def test_repr_includes_source(self) -> None:
        meta = self._make_meta()
        assert "source=" in repr(meta)


# ---------------------------------------------------------------------------
# CredentialStore repr
# ---------------------------------------------------------------------------


class TestCredentialStoreRepr:
    def test_repr_does_not_raise(self) -> None:
        pool = _make_pool()
        store = CredentialStore(pool)
        r = repr(store)
        assert "CredentialStore" in r

    def test_repr_includes_fallback_pool_count(self) -> None:
        pool = _make_pool()
        fallback = _make_pool()
        store = CredentialStore(pool, fallback_pools=[fallback])
        r = repr(store)
        assert "fallback_pools=1" in r


# ---------------------------------------------------------------------------
# _ensure_utc helper
# ---------------------------------------------------------------------------


class TestEnsureUtc:
    def test_attaches_utc_to_naive_datetime(self) -> None:
        naive = datetime(2026, 1, 1, 12, 0, 0)
        result = _ensure_utc(naive)
        assert result.tzinfo == UTC

    def test_leaves_aware_datetime_unchanged(self) -> None:
        aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = _ensure_utc(aware)
        assert result == aware
        assert result.tzinfo == UTC


# ---------------------------------------------------------------------------
# Thread-safety / concurrency: multiple acquire() calls don't cross
# ---------------------------------------------------------------------------


class TestConcurrency:
    async def test_store_acquires_pool_each_time(self) -> None:
        pool = _make_pool()
        store = CredentialStore(pool)
        await store.store("K1", "v1")
        await store.store("K2", "v2")
        assert pool.acquire.call_count == 2

    async def test_load_acquires_pool_each_time(self) -> None:
        pool = _make_pool(fetchrow_return=None)
        store = CredentialStore(pool)
        await store.load("K1")
        await store.load("K2")
        assert pool.acquire.call_count == 2


class TestSharedDbHelpers:
    def test_shared_db_name_from_env_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert shared_db_name_from_env() == "butler_shared"

    def test_shared_db_name_from_env_custom(self) -> None:
        with patch.dict(os.environ, {"BUTLER_SHARED_DB_NAME": "my_shared"}):
            assert shared_db_name_from_env() == "my_shared"

    def test_legacy_shared_db_name_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert legacy_shared_db_name_from_env() == "butler_general"

    def test_legacy_shared_db_name_custom(self) -> None:
        with patch.dict(os.environ, {"BUTLER_LEGACY_SHARED_DB_NAME": "legacy_db"}):
            assert legacy_shared_db_name_from_env() == "legacy_db"

    def test_is_missing_table_error_detection(self) -> None:
        exc = RuntimeError('relation "butler_secrets" does not exist')
        assert _is_missing_table_error(exc) is True

    async def test_backfill_shared_secrets_inserts_only_missing_keys(self) -> None:
        legacy_row_existing = _make_row(
            secret_key="KEEP",
            secret_value="v1",
            category="general",
            description=None,
            is_sensitive=True,
            created_at=_NOW,
            updated_at=_NOW,
            expires_at=None,
        )
        legacy_row_new = _make_row(
            secret_key="NEW",
            secret_value="v2",
            category="google",
            description="desc",
            is_sensitive=False,
            created_at=_NOW,
            updated_at=_NOW,
            expires_at=None,
        )
        legacy_pool = _make_pool(fetch_return=[legacy_row_existing, legacy_row_new])

        shared_conn = AsyncMock()
        # First two execute calls are schema ensure DDL, then two inserts.
        shared_conn.execute.side_effect = ["DDL", "DDL", "INSERT 0 0", "INSERT 0 1"]
        shared_cm = AsyncMock()
        shared_cm.__aenter__ = AsyncMock(return_value=shared_conn)
        shared_cm.__aexit__ = AsyncMock(return_value=False)
        shared_pool = MagicMock()
        shared_pool.acquire.return_value = shared_cm

        inserted = await backfill_shared_secrets(shared_pool, legacy_pool)

        assert inserted == 1
        assert legacy_pool.acquire.call_count == 1
        assert shared_pool.acquire.call_count == 1

    async def test_backfill_shared_secrets_handles_missing_legacy_table(self) -> None:
        legacy_conn = AsyncMock()
        legacy_conn.fetch.side_effect = RuntimeError('relation "butler_secrets" does not exist')
        legacy_cm = AsyncMock()
        legacy_cm.__aenter__ = AsyncMock(return_value=legacy_conn)
        legacy_cm.__aexit__ = AsyncMock(return_value=False)
        legacy_pool = MagicMock()
        legacy_pool.acquire.return_value = legacy_cm

        shared_pool = _make_pool()
        inserted = await backfill_shared_secrets(shared_pool, legacy_pool)

        assert inserted == 0
