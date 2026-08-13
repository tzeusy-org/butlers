from __future__ import annotations

import uuid
from contextlib import asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.tool_call_capture import (
    reset_current_runtime_butler_name,
    reset_current_runtime_session_id,
    reset_current_runtime_trigger_source,
    set_current_runtime_butler_name,
    set_current_runtime_session_id,
    set_current_runtime_trigger_source,
)
from butlers.modules.memory import storage
from butlers.modules.memory.tools import context

pytestmark = pytest.mark.unit


class _PoolStub:
    def __init__(self, conn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


@contextmanager
def _runtime_write_context(*, butler: str, session_id: uuid.UUID, trigger_source: str):
    butler_token = set_current_runtime_butler_name(butler)
    session_token = set_current_runtime_session_id(str(session_id))
    trigger_token = set_current_runtime_trigger_source(trigger_source)
    try:
        yield
    finally:
        reset_current_runtime_trigger_source(trigger_token)
        reset_current_runtime_session_id(session_token)
        reset_current_runtime_butler_name(butler_token)


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

    async def test_exact_consolidation_trigger_skips_automatic_placeholder(self) -> None:
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _PoolStub(conn)
        engine = MagicMock()
        engine.embed.return_value = [0.1] * 384

        with _runtime_write_context(
            butler="health",
            session_id=uuid.uuid4(),
            trigger_source="schedule:consolidation",
        ):
            source_butler, source_episode_id = await storage.resolve_write_provenance(
                pool,
                engine,
                tenant_id="shared",
                request_id="req-consolidation",
            )

        assert source_butler == "health"
        assert source_episode_id is None
        conn.execute.assert_not_awaited()

    @pytest.mark.parametrize(
        "trigger_source",
        ["schedule:daily_digest", "schedule:consolidation:retry", "trigger"],
    )
    async def test_non_exact_consolidation_triggers_keep_automatic_placeholder(
        self, trigger_source: str
    ) -> None:
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)
        pool = _PoolStub(conn)
        engine = MagicMock()
        engine.embed.return_value = [0.1] * 384

        with _runtime_write_context(
            butler="health",
            session_id=uuid.uuid4(),
            trigger_source=trigger_source,
        ):
            source_butler, source_episode_id = await storage.resolve_write_provenance(
                pool,
                engine,
                tenant_id="shared",
                request_id="req-positive-control",
            )

        assert source_butler == "health"
        assert isinstance(source_episode_id, uuid.UUID)
        assert conn.execute.await_count == 1
        assert "INSERT INTO episodes" in conn.execute.await_args.args[0]

    async def test_explicit_source_episode_is_preserved_during_consolidation(self) -> None:
        explicit_episode_id = uuid.uuid4()
        conn = AsyncMock()
        pool = _PoolStub(conn)
        engine = MagicMock()

        with _runtime_write_context(
            butler="health",
            session_id=uuid.uuid4(),
            trigger_source="schedule:consolidation",
        ):
            source_butler, source_episode_id = await storage.resolve_write_provenance(
                pool,
                engine,
                source_episode_id=explicit_episode_id,
                tenant_id="shared",
                request_id="req-explicit",
            )

        assert source_butler == "health"
        assert source_episode_id == explicit_episode_id
        conn.fetchrow.assert_not_awaited()
        conn.execute.assert_not_awaited()

    async def test_existing_same_session_episode_is_preserved_during_consolidation(self) -> None:
        existing_episode_id = uuid.uuid4()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"id": existing_episode_id})
        pool = _PoolStub(conn)
        engine = MagicMock()

        with _runtime_write_context(
            butler="health",
            session_id=uuid.uuid4(),
            trigger_source="schedule:consolidation",
        ):
            source_butler, source_episode_id = await storage.resolve_write_provenance(
                pool,
                engine,
                tenant_id="shared",
                request_id="req-existing",
            )

        assert source_butler == "health"
        assert source_episode_id == existing_episode_id
        conn.execute.assert_not_awaited()


class TestRecentEpisodeFiltering:
    async def test_excludes_provenance_placeholders_with_a_null_safe_predicate(self) -> None:
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])

        result = await context._fetch_recent_episodes(pool, "health", "shared")

        assert result == []
        query = pool.fetch.await_args.args[0]
        assert "metadata->>'provenance_placeholder' IS DISTINCT FROM 'true'" in query


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


class TestConsolidationNarrativeEdgeBoundary:
    @pytest.mark.parametrize(
        "predicate",
        ("planned_dinner_with", "wake_coordination", "social_exchange_with"),
    )
    def test_classifies_only_owner_approved_v1_predicates(self, predicate: str) -> None:
        assert storage.classify_consolidation_narrative_edge(predicate) == predicate
        assert storage.classify_consolidation_narrative_edge("co_hosted") is None

    @pytest.mark.parametrize(
        "predicate",
        (
            "is_planned_dinner_with",
            "planned-dinner-with",
            "planned dinner with",
        ),
    )
    def test_classification_rejects_non_exact_allowlist_aliases(self, predicate: str) -> None:
        assert storage.classify_consolidation_narrative_edge(predicate) is None

    @pytest.mark.parametrize(
        "predicate",
        (
            ["planned_dinner_with"],
            {"name": "planned_dinner_with"},
        ),
        ids=("json-list", "json-object"),
    )
    async def test_direct_storage_fails_closed_for_structured_predicate(
        self,
        predicate: object,
    ) -> None:
        pool = MagicMock()
        engine = MagicMock()

        classification = storage.classify_consolidation_narrative_edge(predicate)

        assert classification is None
        with pytest.raises(ValueError, match="classification.*unavailable"):
            await storage.store_fact(
                pool,
                subject="Alice",
                predicate=predicate,
                content="Dinner next Friday",
                embedding_engine=engine,
                entity_id=uuid.uuid4(),
                object_entity_id=uuid.uuid4(),
                enforce_consolidation_edge_allowlist=True,
                consolidation_edge_classification=classification,
            )

        engine.embed.assert_not_called()
        pool.acquire.assert_not_called()

    async def test_direct_storage_fails_closed_for_unavailable_edge_classification(self) -> None:
        pool = MagicMock()
        engine = MagicMock()

        with pytest.raises(ValueError, match="classification.*unavailable"):
            await storage.store_fact(
                pool,
                subject="Alice",
                predicate="planned_dinner_with",
                content="Dinner next Friday",
                embedding_engine=engine,
                entity_id=uuid.uuid4(),
                object_entity_id=uuid.uuid4(),
                enforce_consolidation_edge_allowlist=True,
                consolidation_edge_classification=None,
            )

        engine.embed.assert_not_called()
        pool.acquire.assert_not_called()

    async def test_direct_storage_rejects_a_mismatched_edge_classification(self) -> None:
        pool = MagicMock()
        engine = MagicMock()

        with pytest.raises(ValueError, match="not owner-approved"):
            await storage.store_fact(
                pool,
                subject="Alice",
                predicate="co_hosted",
                content="Co-hosted a podcast",
                embedding_engine=engine,
                entity_id=uuid.uuid4(),
                object_entity_id=uuid.uuid4(),
                enforce_consolidation_edge_allowlist=True,
                consolidation_edge_classification="planned_dinner_with",
            )

        engine.embed.assert_not_called()
        pool.acquire.assert_not_called()
