"""Tests for writing MCP tools and EmbeddingEngine initialization in tools.py."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Load tools module (mocking sentence_transformers first)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Load tools module
# ---------------------------------------------------------------------------
from butlers.tools.memory import (
    _helpers,
    get_embedding_engine,
    memory_store_episode,
    memory_store_fact,
    memory_store_rule,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Return an AsyncMock asyncpg pool."""
    return AsyncMock()


@pytest.fixture()
def mock_embedding_engine() -> MagicMock:
    """Return a MagicMock EmbeddingEngine."""
    return MagicMock()


# ---------------------------------------------------------------------------
# Tests — memory_store_episode
# ---------------------------------------------------------------------------


class TestMemoryStoreEpisode:
    """Tests for memory_store_episode() tool wrapper."""

    @pytest.fixture(autouse=True)
    def mock_get_engine(self):
        """Mock get_embedding_engine to avoid loading real model."""
        with patch("butlers.tools.memory.writing.get_embedding_engine") as mock:
            yield mock

    async def test_delegates_to_storage(
        self, mock_pool: AsyncMock, mock_get_engine: MagicMock
    ) -> None:
        """memory_store_episode delegates to _storage.store_episode."""
        episode_id = uuid.uuid4()
        expires_at = datetime.now(UTC)
        storage_result = {"id": episode_id, "expires_at": expires_at}

        with patch.object(_helpers._storage, "store_episode", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            await memory_store_episode(mock_pool, "test content", "test-butler")
            mock_store.assert_awaited_once_with(
                mock_pool,
                "test content",
                "test-butler",
                mock_get_engine.return_value,
                session_id=None,
                importance=5.0,
            )

    async def test_returns_id_as_string(self, mock_pool: AsyncMock) -> None:
        """Result dict should contain 'id' as a string."""
        episode_id = uuid.uuid4()
        expires_at = datetime.now(UTC)
        storage_result = {"id": episode_id, "expires_at": expires_at}

        with patch.object(_helpers._storage, "store_episode", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            result = await memory_store_episode(mock_pool, "test", "butler")
            assert result["id"] == str(episode_id)

    async def test_returns_expires_at_as_isoformat(self, mock_pool: AsyncMock) -> None:
        """Result dict should contain 'expires_at' as ISO 8601 string."""
        episode_id = uuid.uuid4()
        expires_at = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        storage_result = {"id": episode_id, "expires_at": expires_at}

        with patch.object(_helpers._storage, "store_episode", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            result = await memory_store_episode(mock_pool, "test", "butler")
            assert result["expires_at"] == expires_at.isoformat()

    async def test_passes_session_id(self, mock_pool: AsyncMock) -> None:
        """session_id kwarg is forwarded to storage."""
        storage_result = {"id": uuid.uuid4(), "expires_at": datetime.now(UTC)}
        sid = uuid.uuid4()
        sid_str = str(sid)

        with patch.object(_helpers._storage, "store_episode", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            await memory_store_episode(mock_pool, "test", "butler", session_id=sid_str)
            call_kwargs = mock_store.call_args.kwargs
            assert call_kwargs["session_id"] == sid

    async def test_passes_importance(self, mock_pool: AsyncMock) -> None:
        """importance kwarg is forwarded to storage."""
        storage_result = {"id": uuid.uuid4(), "expires_at": datetime.now(UTC)}

        with patch.object(_helpers._storage, "store_episode", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            await memory_store_episode(mock_pool, "test", "butler", importance=8.5)
            call_kwargs = mock_store.call_args.kwargs
            assert call_kwargs["importance"] == 8.5

    async def test_default_importance_is_five(self, mock_pool: AsyncMock) -> None:
        """Default importance should be 5.0 when not specified."""
        storage_result = {"id": uuid.uuid4(), "expires_at": datetime.now(UTC)}

        with patch.object(_helpers._storage, "store_episode", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            await memory_store_episode(mock_pool, "test", "butler")
            call_kwargs = mock_store.call_args.kwargs
            assert call_kwargs["importance"] == 5.0

    async def test_result_has_expected_keys(self, mock_pool: AsyncMock) -> None:
        """Result dict should have exactly 'id' and 'expires_at' keys."""
        storage_result = {"id": uuid.uuid4(), "expires_at": datetime.now(UTC)}

        with patch.object(_helpers._storage, "store_episode", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            result = await memory_store_episode(mock_pool, "test", "butler")
            assert set(result.keys()) == {"id", "expires_at"}


# ---------------------------------------------------------------------------
# Tests — memory_store_fact
# ---------------------------------------------------------------------------


class TestMemoryStoreFact:
    """Tests for memory_store_fact() tool wrapper."""

    async def test_delegates_to_storage(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """memory_store_fact delegates to _storage.store_fact."""
        fact_id = uuid.uuid4()
        storage_result = {"id": fact_id}

        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            await memory_store_fact(mock_pool, mock_embedding_engine, "user", "name", "Alice")
            mock_store.assert_awaited_once_with(
                mock_pool,
                "user",
                "name",
                "Alice",
                mock_embedding_engine,
                importance=5.0,
                permanence="standard",
                scope="global",
                tags=None,
            )

    async def test_returns_id_as_string(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """Result dict should contain 'id' as a string."""
        fact_id = uuid.uuid4()
        storage_result = {"id": fact_id}

        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            result = await memory_store_fact(
                mock_pool, mock_embedding_engine, "user", "name", "Alice"
            )
            assert result["id"] == str(fact_id)

    async def test_superseded_id_none_when_absent(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """superseded_id should be None when storage result has no superseded_id."""
        storage_result = {"id": uuid.uuid4()}

        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            result = await memory_store_fact(
                mock_pool, mock_embedding_engine, "user", "name", "Alice"
            )
            assert result["superseded_id"] is None

    async def test_superseded_id_returned_when_present(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """superseded_id should be a string UUID when storage returns one."""
        fact_id = uuid.uuid4()
        old_id = uuid.uuid4()
        storage_result = {"id": fact_id, "superseded_id": old_id}

        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            result = await memory_store_fact(
                mock_pool, mock_embedding_engine, "user", "name", "Alice"
            )
            assert result["superseded_id"] == str(old_id)

    async def test_passes_custom_kwargs(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """Custom kwargs are forwarded to storage."""
        storage_result = {"id": uuid.uuid4()}

        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            await memory_store_fact(
                mock_pool,
                mock_embedding_engine,
                "user",
                "city",
                "Berlin",
                importance=9.0,
                permanence="stable",
                scope="butler:editor",
                tags=["location"],
            )
            call_kwargs = mock_store.call_args.kwargs
            assert call_kwargs["importance"] == 9.0
            assert call_kwargs["permanence"] == "stable"
            assert call_kwargs["scope"] == "butler:editor"
            assert call_kwargs["tags"] == ["location"]

    async def test_result_has_expected_keys(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """Result dict should have exactly 'id' and 'superseded_id' keys."""
        storage_result = {"id": uuid.uuid4()}

        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            result = await memory_store_fact(
                mock_pool, mock_embedding_engine, "user", "name", "Alice"
            )
            assert set(result.keys()) == {"id", "superseded_id"}


# ---------------------------------------------------------------------------
# Tests — memory_store_rule
# ---------------------------------------------------------------------------


class TestMemoryStoreRule:
    """Tests for memory_store_rule() tool wrapper."""

    async def test_delegates_to_storage(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """memory_store_rule delegates to _storage.store_rule."""
        rule_id = uuid.uuid4()
        storage_result = {"id": rule_id}

        with patch.object(_helpers._storage, "store_rule", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            await memory_store_rule(mock_pool, mock_embedding_engine, "Always greet the user")
            mock_store.assert_awaited_once_with(
                mock_pool,
                "Always greet the user",
                mock_embedding_engine,
                scope="global",
                tags=None,
            )

    async def test_returns_id_as_string(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """Result dict should contain 'id' as a string."""
        rule_id = uuid.uuid4()
        storage_result = {"id": rule_id}

        with patch.object(_helpers._storage, "store_rule", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            result = await memory_store_rule(mock_pool, mock_embedding_engine, "Be helpful")
            assert result["id"] == str(rule_id)

    async def test_passes_custom_scope(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """Custom scope is forwarded to storage."""
        storage_result = {"id": uuid.uuid4()}

        with patch.object(_helpers._storage, "store_rule", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            await memory_store_rule(
                mock_pool, mock_embedding_engine, "Test rule", scope="butler:email"
            )
            call_kwargs = mock_store.call_args.kwargs
            assert call_kwargs["scope"] == "butler:email"

    async def test_passes_custom_tags(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """Custom tags are forwarded to storage."""
        storage_result = {"id": uuid.uuid4()}

        with patch.object(_helpers._storage, "store_rule", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            await memory_store_rule(
                mock_pool,
                mock_embedding_engine,
                "Test rule",
                tags=["safety", "ux"],
            )
            call_kwargs = mock_store.call_args.kwargs
            assert call_kwargs["tags"] == ["safety", "ux"]

    async def test_result_has_only_id_key(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """Result dict should have exactly one key: 'id'."""
        storage_result = {"id": uuid.uuid4()}

        with patch.object(_helpers._storage, "store_rule", new_callable=AsyncMock) as mock_store:
            mock_store.return_value = storage_result
            result = await memory_store_rule(mock_pool, mock_embedding_engine, "Test")
            assert set(result.keys()) == {"id"}


# ---------------------------------------------------------------------------
# Tests — get_embedding_engine singleton
# ---------------------------------------------------------------------------


class TestGetEmbeddingEngine:
    """Tests for the EmbeddingEngine singleton factory."""

    def test_returns_engine_instance(self) -> None:
        """get_embedding_engine should return an EmbeddingEngine instance."""
        # Reset the singleton before testing
        _helpers._embedding_engine = None
        with patch.object(_helpers, "EmbeddingEngine", return_value=MagicMock()) as mock_cls:
            engine = get_embedding_engine()
            mock_cls.assert_called_once()
            assert engine is mock_cls.return_value

    def test_returns_same_instance_on_second_call(self) -> None:
        """get_embedding_engine should return the same singleton on repeated calls."""
        _helpers._embedding_engine = None
        sentinel = MagicMock()
        with patch.object(_helpers, "EmbeddingEngine", return_value=sentinel):
            first = get_embedding_engine()
            second = get_embedding_engine()
            assert first is second
            assert first is sentinel

    def test_singleton_constructor_called_only_once(self) -> None:
        """EmbeddingEngine() should be called exactly once across multiple calls."""
        _helpers._embedding_engine = None
        with patch.object(_helpers, "EmbeddingEngine", return_value=MagicMock()) as mock_cls:
            get_embedding_engine()
            get_embedding_engine()
            get_embedding_engine()
            mock_cls.assert_called_once()

    def test_reset_singleton_allows_recreation(self) -> None:
        """Setting _embedding_engine to None allows a fresh instance."""
        _helpers._embedding_engine = None
        first_sentinel = MagicMock()
        second_sentinel = MagicMock()

        with patch.object(_helpers, "EmbeddingEngine", return_value=first_sentinel):
            first = get_embedding_engine()

        _helpers._embedding_engine = None

        with patch.object(_helpers, "EmbeddingEngine", return_value=second_sentinel):
            second = get_embedding_engine()

        assert first is first_sentinel
        assert second is second_sentinel
        assert first is not second
