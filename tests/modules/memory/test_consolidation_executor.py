"""Regression tests for applying parsed memory consolidation actions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from butlers.modules.memory import consolidation_executor
from butlers.modules.memory.consolidation_parser import (
    ConsolidationResult,
    NewFact,
    UpdatedFact,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_execute_consolidation_forwards_new_temporal_timestamp_only(monkeypatch) -> None:
    # Regression for the live relationship consolidation failure: a *new*
    # upcoming_event needs valid_at forwarded to store_fact.
    new_valid_at = datetime(2026, 7, 21, 9, 30, tzinfo=UTC)
    stored_kwargs: list[dict] = []

    async def _store_fact(*args, **kwargs):
        stored_kwargs.append(kwargs)
        return {"id": uuid.uuid4(), "supersedes_id": None}

    monkeypatch.setattr(consolidation_executor, "store_fact", _store_fact)
    monkeypatch.setattr(
        consolidation_executor,
        "_lookup_episode_ttl_days",
        AsyncMock(return_value=7),
    )

    parsed = ConsolidationResult(
        new_facts=[
            NewFact(
                subject="person",
                predicate="upcoming_event",
                content="event details",
                valid_at=new_valid_at,
            )
        ],
        updated_facts=[
            UpdatedFact(
                target_id=str(uuid.uuid4()),
                subject="person",
                predicate="current_city",
                content="Singapore",
            )
        ],
    )

    result = await consolidation_executor.execute_consolidation(
        pool=object(),
        embedding_engine=object(),
        parsed=parsed,
        source_episode_ids=[],
        butler_name="relationship",
    )

    assert result["errors"] == []
    assert result["facts_created"] == 1
    assert result["facts_updated"] == 1
    assert stored_kwargs[0]["valid_at"] == new_valid_at
    assert "valid_at" not in stored_kwargs[1]
