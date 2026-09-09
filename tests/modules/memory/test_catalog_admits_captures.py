"""A captured note is visible to other butlers through public.memory_catalog
(bu-2jtfw.9): _fetch_fleet_knowledge, the cross-butler discovery path memory
context assembly already uses, must surface it for a non-general butler.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
async def core_pool(postgres_container):
    from butlers.testing.migration import create_migrated_test_pool

    pool = await create_migrated_test_pool(postgres_container, chains=["core"])
    try:
        yield pool
    finally:
        await pool.close()


class _FakeEmbeddingEngine:
    """Deterministic 384-dim embedding stub -- semantic branch just needs a
    vector of the right shape; the keyword branch is what actually matches.
    """

    def embed(self, text: str) -> list[float]:
        return [0.01] * 384


async def test_captured_note_appears_in_fleet_knowledge_for_other_butler(core_pool) -> None:
    from butlers.modules.memory.search import resolve_catalog_read_policy
    from butlers.modules.memory.tools.context import _fetch_fleet_knowledge

    source_id = uuid.uuid4()
    await core_pool.execute(
        """
        INSERT INTO public.memory_catalog
            (source_schema, source_table, source_id, source_butler,
             tenant_id, summary, search_vector, memory_type)
        VALUES ('general', 'collection_items', $1, 'general',
                'shared', $2, to_tsvector('english', $2), 'capture')
        """,
        source_id,
        "a captured note about the quarterly budget review",
    )

    results = await _fetch_fleet_knowledge(
        core_pool,
        _FakeEmbeddingEngine(),
        "quarterly budget review",
        butler="health",
        tenant_id="shared",
        read_policy=resolve_catalog_read_policy("normal"),
    )

    matches = [r for r in results if r["source_id"] == source_id]
    assert len(matches) == 1
    assert matches[0]["source_butler"] == "general"
    assert matches[0]["memory_type"] == "capture"


async def test_own_butler_captures_excluded_from_fleet_knowledge(core_pool) -> None:
    """_fetch_fleet_knowledge excludes the calling butler's own knowledge."""
    from butlers.modules.memory.search import resolve_catalog_read_policy
    from butlers.modules.memory.tools.context import _fetch_fleet_knowledge

    source_id = uuid.uuid4()
    await core_pool.execute(
        """
        INSERT INTO public.memory_catalog
            (source_schema, source_table, source_id, source_butler,
             tenant_id, summary, search_vector, memory_type)
        VALUES ('general', 'collection_items', $1, 'general',
                'shared', $2, to_tsvector('english', $2), 'capture')
        """,
        source_id,
        "a captured note only general itself should not see back",
    )

    results = await _fetch_fleet_knowledge(
        core_pool,
        _FakeEmbeddingEngine(),
        "captured note only general",
        butler="general",
        tenant_id="shared",
        read_policy=resolve_catalog_read_policy("normal"),
    )

    assert all(r["source_id"] != source_id for r in results)
