"""Regression tests for applying parsed memory consolidation actions."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from butlers.modules.memory import consolidation_executor
from butlers.modules.memory.consolidation_parser import (
    ConsolidationResult,
    NewFact,
    NewRule,
    UpdatedFact,
    parse_consolidation_output,
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
    assert stored_kwargs[0]["enforce_consolidation_edge_allowlist"] is True
    assert stored_kwargs[0]["consolidation_edge_classification"] == "planned_dinner_with"


@pytest.mark.asyncio
async def test_execute_consolidation_defers_unapproved_edge_to_storage_boundary(
    monkeypatch,
) -> None:
    object_entity_id = uuid.uuid4()

    async def _store_fact(*args, **kwargs):
        assert kwargs["predicate"] == "works_at"
        assert kwargs["object_entity_id"] == object_entity_id
        assert kwargs["enforce_consolidation_edge_allowlist"] is True
        assert kwargs["consolidation_edge_classification"] is None
        raise ValueError("consolidation edge predicate is not owner-approved")

    store_fact_mock = AsyncMock(side_effect=_store_fact)
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
                object_entity_id=str(object_entity_id),
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
    store_fact_mock.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "predicate",
    (
        ["planned_dinner_with"],
        {"name": "planned_dinner_with"},
    ),
    ids=("json-list", "json-object"),
)
async def test_execute_consolidation_defers_structured_edge_predicate_to_storage_boundary(
    monkeypatch,
    predicate: object,
) -> None:
    object_entity_id = uuid.uuid4()
    parsed = parse_consolidation_output(
        json.dumps(
            {
                "new_facts": [
                    {
                        "subject": "person",
                        "predicate": predicate,
                        "content": "coordination context",
                        "entity_id": str(uuid.uuid4()),
                        "object_entity_id": str(object_entity_id),
                    }
                ]
            }
        )
    )
    assert parsed.parse_errors == []
    assert parsed.new_facts[0].predicate == predicate

    async def _store_fact(*args, **kwargs):
        assert kwargs["predicate"] == predicate
        assert kwargs["object_entity_id"] == object_entity_id
        assert kwargs["enforce_consolidation_edge_allowlist"] is True
        assert kwargs["consolidation_edge_classification"] is None
        raise ValueError("consolidation edge classification is unavailable")

    store_fact_mock = AsyncMock(side_effect=_store_fact)
    monkeypatch.setattr(consolidation_executor, "store_fact", store_fact_mock)
    monkeypatch.setattr(
        consolidation_executor,
        "_lookup_episode_ttl_days",
        AsyncMock(return_value=7),
    )

    result = await consolidation_executor.execute_consolidation(
        pool=object(),
        embedding_engine=object(),
        parsed=parsed,
        source_episode_ids=[],
        butler_name="relationship",
    )

    assert result["facts_created"] == 0
    assert result["errors"] == [f"Failed to store new fact (person/{predicate})"]
    store_fact_mock.assert_awaited_once()


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


class _AtomicTransaction:
    def __init__(self, connection: _AtomicConnection) -> None:
        self._connection = connection
        self._snapshot: list[str] = []

    async def __aenter__(self) -> _AtomicTransaction:
        self._snapshot = list(self._connection.persisted_artifacts)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self._connection.persisted_artifacts[:] = self._snapshot
            self._connection.rollbacks += 1
        else:
            self._connection.commits += 1
        return False


class _AtomicConnection:
    def __init__(self) -> None:
        self.persisted_artifacts: list[str] = []
        self.commits = 0
        self.rollbacks = 0
        self.executes: list[tuple] = []

    def transaction(self) -> _AtomicTransaction:
        return _AtomicTransaction(self)

    async def execute(self, *args) -> str:
        self.executes.append(args)
        return "UPDATE 0"


class _AtomicAcquire:
    def __init__(self, connection: _AtomicConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _AtomicConnection:
        return self._connection

    async def __aexit__(self, *exc) -> bool:
        return False


class _AtomicPool:
    def __init__(self) -> None:
        self.connection = _AtomicConnection()

    def acquire(self) -> _AtomicAcquire:
        return _AtomicAcquire(self.connection)

    async def execute(self, *args) -> str:
        return await self.connection.execute(*args)


@pytest.mark.asyncio
async def test_invalid_artifact_evidence_stops_before_any_write(monkeypatch) -> None:
    source_episode_id = uuid.uuid4()
    foreign_episode_id = uuid.uuid4()
    store_fact_mock = AsyncMock(return_value={"id": uuid.uuid4(), "supersedes_id": None})
    monkeypatch.setattr(consolidation_executor, "store_fact", store_fact_mock)
    monkeypatch.setattr(
        consolidation_executor,
        "_lookup_episode_ttl_days",
        AsyncMock(return_value=7),
    )

    for evidence in (
        None,
        [],
        ["not-a-uuid"],
        [str(source_episode_id), str(source_episode_id)],
        [str(foreign_episode_id)],
    ):
        parsed = ConsolidationResult(
            new_facts=[
                NewFact(
                    subject="owner",
                    predicate="preference",
                    content="likes quiet dinners",
                    evidence_episode_ids=evidence,
                )
            ]
        )

        with pytest.raises(consolidation_executor.ConsolidationEvidenceValidationError):
            await consolidation_executor.execute_consolidation(
                pool=AsyncMock(),
                embedding_engine=object(),
                parsed=parsed,
                source_episode_ids=[source_episode_id],
                butler_name="relationship",
            )

    store_fact_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_later_artifact_evidence_stops_before_any_write(monkeypatch) -> None:
    source_episode_id = uuid.uuid4()
    foreign_episode_id = uuid.uuid4()
    store_fact_mock = AsyncMock(return_value={"id": uuid.uuid4(), "supersedes_id": None})
    monkeypatch.setattr(consolidation_executor, "store_fact", store_fact_mock)

    parsed = ConsolidationResult(
        new_facts=[
            NewFact(
                subject="owner",
                predicate="preference",
                content="likes quiet dinners",
                evidence_episode_ids=[str(source_episode_id)],
            )
        ],
        new_rules=[
            NewRule(
                content="Prefer quiet venues for dinner planning.",
                evidence_episode_ids=[str(foreign_episode_id)],
            )
        ],
    )

    with pytest.raises(consolidation_executor.ConsolidationEvidenceValidationError):
        await consolidation_executor.execute_consolidation(
            pool=AsyncMock(),
            embedding_engine=object(),
            parsed=parsed,
            source_episode_ids=[source_episode_id],
            butler_name="relationship",
        )

    store_fact_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_each_artifact_links_only_its_validated_episode_evidence(monkeypatch) -> None:
    first_episode_id = uuid.uuid4()
    second_episode_id = uuid.uuid4()
    fact_id = uuid.uuid4()
    rule_id = uuid.uuid4()
    pool = _AtomicPool()
    links: list[tuple] = []

    monkeypatch.setattr(
        consolidation_executor,
        "store_fact",
        AsyncMock(return_value={"id": fact_id, "supersedes_id": None}),
    )
    monkeypatch.setattr(consolidation_executor, "store_rule", AsyncMock(return_value=rule_id))
    monkeypatch.setattr(
        consolidation_executor,
        "create_link",
        AsyncMock(side_effect=lambda *args: links.append(args)),
    )
    monkeypatch.setattr(
        consolidation_executor,
        "_lookup_episode_ttl_days",
        AsyncMock(return_value=7),
    )

    result = await consolidation_executor.execute_consolidation(
        pool=pool,
        embedding_engine=object(),
        parsed=ConsolidationResult(
            new_facts=[
                NewFact(
                    subject="owner",
                    predicate="preference",
                    content="likes quiet dinners",
                    evidence_episode_ids=[str(first_episode_id)],
                )
            ],
            new_rules=[
                NewRule(
                    content="Prefer quiet venues for dinner planning.",
                    evidence_episode_ids=[str(second_episode_id)],
                )
            ],
        ),
        source_episode_ids=[first_episode_id, second_episode_id],
        butler_name="relationship",
    )

    assert result["errors"] == []
    assert [link[1:] for link in links] == [
        ("fact", fact_id, "episode", first_episode_id, "derived_from"),
        ("rule", rule_id, "episode", second_episode_id, "derived_from"),
    ]
    assert pool.connection.commits == 2


@pytest.mark.asyncio
async def test_updated_fact_links_only_its_validated_episode_evidence(monkeypatch) -> None:
    first_episode_id = uuid.uuid4()
    second_episode_id = uuid.uuid4()
    target_id = uuid.uuid4()
    new_fact_id = uuid.uuid4()
    pool = _AtomicPool()
    pool.fetchrow = AsyncMock(
        return_value={
            "subject": "owner",
            "predicate": "preference",
            "entity_id": uuid.uuid4(),
            "scope": "relationship",
        }
    )
    pool.fetchval = AsyncMock(return_value=False)
    links: list[tuple] = []

    monkeypatch.setattr(
        consolidation_executor,
        "store_fact",
        AsyncMock(return_value={"id": new_fact_id, "supersedes_id": target_id}),
    )
    monkeypatch.setattr(
        consolidation_executor,
        "create_link",
        AsyncMock(side_effect=lambda *args: links.append(args)),
    )
    monkeypatch.setattr(
        consolidation_executor,
        "_lookup_episode_ttl_days",
        AsyncMock(return_value=7),
    )

    result = await consolidation_executor.execute_consolidation(
        pool=pool,
        embedding_engine=object(),
        parsed=ConsolidationResult(
            updated_facts=[
                UpdatedFact(
                    target_id=str(target_id),
                    content="prefers quiet dinners",
                    evidence_episode_ids=[str(second_episode_id)],
                )
            ]
        ),
        source_episode_ids=[first_episode_id, second_episode_id],
        butler_name="relationship",
    )

    assert result["errors"] == []
    assert result["facts_updated"] == 1
    assert [link[1:] for link in links] == [
        ("fact", new_fact_id, "episode", second_episode_id, "derived_from")
    ]
    assert pool.connection.commits == 1


@pytest.mark.asyncio
async def test_failed_evidence_link_rolls_back_its_artifact(monkeypatch) -> None:
    source_episode_id = uuid.uuid4()
    pool = _AtomicPool()

    async def _store_fact(*args, **kwargs):
        pool.connection.persisted_artifacts.append("fact")
        return {"id": uuid.uuid4(), "supersedes_id": None}

    monkeypatch.setattr(consolidation_executor, "store_fact", _store_fact)
    monkeypatch.setattr(
        consolidation_executor,
        "create_link",
        AsyncMock(side_effect=RuntimeError("evidence link insert failed")),
    )
    monkeypatch.setattr(
        consolidation_executor,
        "_lookup_episode_ttl_days",
        AsyncMock(return_value=7),
    )

    result = await consolidation_executor.execute_consolidation(
        pool=pool,
        embedding_engine=object(),
        parsed=ConsolidationResult(
            new_facts=[
                NewFact(
                    subject="owner",
                    predicate="preference",
                    content="likes quiet dinners",
                    evidence_episode_ids=[str(source_episode_id)],
                )
            ]
        ),
        source_episode_ids=[source_episode_id],
        butler_name="relationship",
    )

    assert result["facts_created"] == 0
    assert result["errors"] == ["Failed to store new fact (owner/preference)"]
    assert pool.connection.persisted_artifacts == []
    assert pool.connection.rollbacks == 1
