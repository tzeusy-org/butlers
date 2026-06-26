"""Unit coverage for relationship interaction direction handling."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_interaction_log_normalizes_inbound_alias(monkeypatch):
    """MCP callers may use channel vocabulary; storage stays canonical."""
    from butlers.modules.memory import storage
    from butlers.tools.relationship import interactions

    entity_id = uuid.uuid4()
    fact_id = uuid.uuid4()
    occurred_at = datetime(2026, 6, 25, 9, 30, tzinfo=UTC)
    captured: dict[str, object] = {}

    async def fake_resolve(pool, target_id):
        return target_id, None

    async def fake_store_fact(pool, **kwargs):
        captured.update(kwargs)
        return {"id": fact_id}

    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)

    monkeypatch.setattr(interactions, "_resolve_interaction_target", fake_resolve)
    monkeypatch.setattr(interactions, "_get_embedding_engine", lambda: object())
    monkeypatch.setattr(storage, "store_fact", fake_store_fact)

    result = await interactions.interaction_log(
        pool,
        entity_id,
        "email",
        occurred_at=occurred_at,
        direction="inbound",
    )

    assert result["direction"] == "incoming"
    assert captured["metadata"] == {"type": "email", "direction": "incoming"}
    assert pool.fetchrow.await_args.args[4] == "incoming"


async def test_interaction_list_normalizes_outbound_alias(monkeypatch):
    """Direction filters use canonical values even when callers pass aliases."""
    from butlers.tools.relationship import interactions

    entity_id = uuid.uuid4()
    fact_id = uuid.uuid4()

    async def fake_resolve(pool, target_id):
        return target_id, None

    pool = MagicMock()
    pool.fetch = AsyncMock(
        return_value=[
            {
                "id": fact_id,
                "predicate": "interaction_call",
                "content": "Called back",
                "valid_at": datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
                "created_at": datetime(2026, 6, 25, 10, 5, tzinfo=UTC),
                "metadata": {"type": "call", "direction": "outgoing"},
            }
        ]
    )

    monkeypatch.setattr(interactions, "_resolve_interaction_target", fake_resolve)

    result = await interactions.interaction_list(pool, entity_id, direction="outbound")

    assert result[0]["direction"] == "outgoing"
    assert pool.fetch.await_args.args[2] == "outgoing"
