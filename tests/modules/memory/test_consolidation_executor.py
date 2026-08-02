"""Regression tests for applying parsed memory consolidation actions."""

from __future__ import annotations

import logging
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
    pool = AsyncMock()
    pool.fetchval.return_value = False
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
    pool.fetchrow.return_value = {
        "subject": "person",
        "predicate": "current_city",
        "entity_id": None,
        "scope": "relationship",
    }

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
                content="Singapore",
            )
        ],
    )

    result = await consolidation_executor.execute_consolidation(
        pool=pool,
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


@pytest.mark.asyncio
async def test_execute_consolidation_skips_registered_temporal_updated_fact(monkeypatch) -> None:
    target_id = str(uuid.uuid4())
    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "subject": "system",
        "predicate": "status_event",
        "entity_id": uuid.uuid4(),
        "scope": "travel",
    }
    pool.fetchval.return_value = True
    store_fact_mock = AsyncMock(
        return_value={"id": uuid.uuid4(), "supersedes_id": None},
    )

    monkeypatch.setattr(consolidation_executor, "store_fact", store_fact_mock)
    monkeypatch.setattr(
        consolidation_executor,
        "_lookup_episode_ttl_days",
        AsyncMock(return_value=7),
    )

    parsed = ConsolidationResult(
        updated_facts=[
            UpdatedFact(
                target_id=target_id,
                content="new status",
            )
        ],
    )

    result = await consolidation_executor.execute_consolidation(
        pool=pool,
        embedding_engine=object(),
        parsed=parsed,
        source_episode_ids=[],
        butler_name="travel",
    )

    assert result["facts_updated"] == 0
    assert result["errors"] == [f"Skipped temporal updated fact ({target_id})"]
    pool.fetchval.assert_awaited_once_with(
        "SELECT is_temporal FROM predicate_registry "
        "WHERE name = $1 OR $1 = ANY(aliases) "
        "ORDER BY ($1 = ANY(aliases)) DESC LIMIT 1",
        "status_event",
    )
    store_fact_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_consolidation_skips_temporal_updated_fact_by_predicate_alias(
    monkeypatch,
) -> None:
    target_id = str(uuid.uuid4())
    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "subject": "system",
        "predicate": "status",
        "entity_id": uuid.uuid4(),
        "scope": "travel",
    }
    pool.fetchval.side_effect = lambda query, _predicate: "aliases" in query
    store_fact_mock = AsyncMock(
        return_value={"id": uuid.uuid4(), "supersedes_id": None},
    )

    monkeypatch.setattr(consolidation_executor, "store_fact", store_fact_mock)
    monkeypatch.setattr(
        consolidation_executor,
        "_lookup_episode_ttl_days",
        AsyncMock(return_value=7),
    )

    parsed = ConsolidationResult(
        updated_facts=[
            UpdatedFact(
                target_id=target_id,
                content="new status",
            )
        ],
    )

    result = await consolidation_executor.execute_consolidation(
        pool=pool,
        embedding_engine=object(),
        parsed=parsed,
        source_episode_ids=[],
        butler_name="travel",
    )

    assert result["facts_updated"] == 0
    assert result["errors"] == [f"Skipped temporal updated fact ({target_id})"]
    store_fact_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_consolidation_forwards_new_narrative_edge_target(monkeypatch) -> None:
    subject_entity_id = uuid.uuid4()
    object_entity_id = uuid.uuid4()
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
                predicate="planned_dinner_with",
                content="dinner next Friday",
                entity_id=str(subject_entity_id),
                object_entity_id=str(object_entity_id),
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
    assert stored_kwargs[0]["entity_id"] == subject_entity_id
    assert stored_kwargs[0]["object_entity_id"] == object_entity_id


@pytest.mark.asyncio
async def test_execute_consolidation_rejects_registry_relational_edge(monkeypatch) -> None:
    store_fact_mock = AsyncMock(
        return_value={"id": uuid.uuid4(), "supersedes_id": None},
    )
    monkeypatch.setattr(consolidation_executor, "store_fact", store_fact_mock)
    monkeypatch.setattr(
        consolidation_executor,
        "_lookup_episode_ttl_days",
        AsyncMock(return_value=7),
    )

    parsed = ConsolidationResult(
        new_facts=[
            NewFact(
                subject="person",
                predicate="works_at",
                content="engineer",
                entity_id=str(uuid.uuid4()),
                object_entity_id=str(uuid.uuid4()),
            )
        ],
    )

    result = await consolidation_executor.execute_consolidation(
        pool=AsyncMock(),
        embedding_engine=object(),
        parsed=parsed,
        source_episode_ids=[],
        butler_name="relationship",
    )

    assert result["facts_created"] == 0
    assert result["errors"] == ["Failed to store new fact (person/works_at)"]
    store_fact_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_updated_fact_uses_persisted_target_identity(monkeypatch) -> None:
    """The target row supplies every identity field for an existing fact update."""
    target_id = uuid.uuid4()
    persisted_entity_id = uuid.uuid4()
    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "subject": "persisted subject",
        "predicate": "persisted_predicate",
        "entity_id": persisted_entity_id,
        "scope": "persisted_scope",
    }
    pool.fetchval.return_value = False
    stored_kwargs: list[dict] = []
    stored_args: list[tuple] = []

    async def _store_fact(*args, **kwargs):
        stored_args.append(args)
        stored_kwargs.append(kwargs)
        return {"id": uuid.uuid4(), "supersedes_id": target_id}

    monkeypatch.setattr(consolidation_executor, "store_fact", _store_fact)
    monkeypatch.setattr(
        consolidation_executor,
        "_lookup_episode_ttl_days",
        AsyncMock(return_value=7),
    )

    parsed = ConsolidationResult(
        updated_facts=[
            UpdatedFact(
                target_id=str(target_id),
                content="new value",
            )
        ]
    )

    result = await consolidation_executor.execute_consolidation(
        pool=pool,
        embedding_engine=object(),
        parsed=parsed,
        source_episode_ids=[],
        butler_name="travel",
        tenant_id="shared",
    )

    assert result["errors"] == []
    assert result["facts_updated"] == 1
    pool.fetchrow.assert_awaited_once()
    assert pool.fetchrow.await_args.args[1:] == (target_id, "shared", "travel")
    assert stored_args[0][1:4] == (
        "persisted subject",
        "persisted_predicate",
        "new value",
    )
    assert stored_kwargs[0]["entity_id"] == persisted_entity_id
    assert stored_kwargs[0]["scope"] == "persisted_scope"
    assert stored_kwargs[0]["expected_supersedes_id"] == target_id

    target_query = " ".join(pool.fetchrow.await_args.args[0].split())
    assert "tenant_id = $2" in target_query
    assert "source_butler = $3" in target_query
    assert "object_entity_id IS NULL" in target_query
    pool.fetchval.assert_awaited_once_with(
        "SELECT is_temporal FROM predicate_registry "
        "WHERE name = $1 OR $1 = ANY(aliases) "
        "ORDER BY ($1 = ANY(aliases)) DESC LIMIT 1",
        "persisted_predicate",
    )


@pytest.mark.asyncio
async def test_missing_target_is_sanitized_and_later_updates_continue(
    monkeypatch,
    caplog,
) -> None:
    missing_id = uuid.uuid4()
    valid_id = uuid.uuid4()
    entity_id = uuid.uuid4()
    pool = AsyncMock()
    pool.fetchrow.side_effect = [
        None,
        {
            "subject": "persisted subject",
            "predicate": "persisted_predicate",
            "entity_id": entity_id,
            "scope": "travel",
        },
    ]
    pool.fetchval.return_value = False
    store_fact_mock = AsyncMock(
        return_value={"id": uuid.uuid4(), "supersedes_id": valid_id},
    )

    monkeypatch.setattr(consolidation_executor, "store_fact", store_fact_mock)
    monkeypatch.setattr(
        consolidation_executor,
        "_lookup_episode_ttl_days",
        AsyncMock(return_value=7),
    )

    parsed = ConsolidationResult(
        updated_facts=[
            UpdatedFact(
                target_id=str(missing_id),
                content="missing",
            ),
            UpdatedFact(
                target_id=str(valid_id),
                content="valid",
            ),
        ],
    )

    with caplog.at_level(logging.WARNING, logger=consolidation_executor.__name__):
        result = await consolidation_executor.execute_consolidation(
            pool=pool,
            embedding_engine=object(),
            parsed=parsed,
            source_episode_ids=[],
            butler_name="travel",
            tenant_id="shared",
        )

    assert result["facts_updated"] == 1
    assert result["errors"] == [f"Failed to update fact ({missing_id})"]
    store_fact_mock.assert_awaited_once()
    assert store_fact_mock.await_args.kwargs["expected_supersedes_id"] == valid_id
    assert [record.levelno for record in caplog.records] == [logging.WARNING]
    assert "Skipping non-live property fact update" in caplog.text


@pytest.mark.asyncio
async def test_post_lookup_stale_target_warns_but_unexpected_store_error_is_error(
    monkeypatch,
    caplog,
) -> None:
    stale_id = uuid.uuid4()
    unexpected_id = uuid.uuid4()
    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "subject": "persisted subject",
        "predicate": "persisted_predicate",
        "entity_id": uuid.uuid4(),
        "scope": "travel",
    }
    pool.fetchval.return_value = False
    store_fact_mock = AsyncMock(
        side_effect=[
            consolidation_executor.StaleSupersessionTargetError(
                f"expected supersession target {stale_id!r} is no longer current"
            ),
            RuntimeError("unexpected storage failure"),
        ],
    )

    monkeypatch.setattr(consolidation_executor, "store_fact", store_fact_mock)
    monkeypatch.setattr(
        consolidation_executor,
        "_lookup_episode_ttl_days",
        AsyncMock(return_value=7),
    )

    parsed = ConsolidationResult(
        updated_facts=[
            UpdatedFact(target_id=str(stale_id), content="stale"),
            UpdatedFact(target_id=str(unexpected_id), content="unexpected"),
        ],
    )

    with caplog.at_level(logging.WARNING, logger=consolidation_executor.__name__):
        result = await consolidation_executor.execute_consolidation(
            pool=pool,
            embedding_engine=object(),
            parsed=parsed,
            source_episode_ids=[],
            butler_name="travel",
            tenant_id="shared",
        )

    assert result["facts_updated"] == 0
    assert result["errors"] == [
        f"Failed to update fact ({stale_id})",
        f"Failed to update fact ({unexpected_id})",
    ]
    assert store_fact_mock.await_count == 2
    assert [record.levelno for record in caplog.records] == [logging.WARNING, logging.ERROR]
    assert f"Skipping stale property fact update {stale_id}" in caplog.text
    assert f"Failed to update fact ({unexpected_id})" in caplog.text
