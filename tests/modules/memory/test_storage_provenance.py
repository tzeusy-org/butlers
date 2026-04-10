from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.memory import storage

pytestmark = pytest.mark.unit


class _PoolStub:
    def __init__(self, conn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


class TestResolveWriteProvenance:
    async def test_uses_runtime_butler_and_creates_session_episode_when_missing(self) -> None:
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _PoolStub(conn)
        engine = MagicMock()
        engine.embed.return_value = [0.1] * 384
        session_id = uuid.uuid4()

        with (
            patch(
                "butlers.modules.memory.storage.get_current_runtime_butler_name",
                return_value="health",
            ),
            patch(
                "butlers.modules.memory.storage.get_current_runtime_session_id",
                return_value=str(session_id),
            ),
            patch.object(
                storage, "_lookup_episode_ttl_days", new_callable=AsyncMock, return_value=7
            ),
        ):
            source_butler, source_episode_id = await storage.resolve_write_provenance(
                pool,
                engine,
                tenant_id="shared",
                request_id="req-123",
            )

        assert source_butler == "health"
        assert isinstance(source_episode_id, uuid.UUID)
        assert conn.execute.await_count == 1
        assert "INSERT INTO episodes" in conn.execute.await_args.args[0]


class TestStoreEpisode:
    async def test_reuses_existing_episode_for_same_session(self) -> None:
        existing_id = uuid.uuid4()
        session_id = uuid.uuid4()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"id": existing_id})
        pool = _PoolStub(conn)
        engine = MagicMock()
        engine.embed.return_value = [0.1] * 384

        with patch.object(
            storage, "_lookup_episode_ttl_days", new_callable=AsyncMock, return_value=7
        ):
            result = await storage.store_episode(
                pool,
                "final session output",
                "general",
                engine,
                session_id=session_id,
                importance=6.0,
                metadata={"source": "runtime"},
                tenant_id="shared",
                request_id="req-456",
            )

        assert result == existing_id
        assert conn.execute.await_count == 1
        assert "UPDATE episodes" in conn.execute.await_args.args[0]
