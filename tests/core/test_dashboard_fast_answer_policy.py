"""Admission is data-only; catalog similarity and visibility never grant a call."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from butlers.core import memory_hooks
from butlers.core.dashboard_fast_answer_catalog import (
    CatalogOutcome,
    resolve_fast_answer_catalog,
    validate_catalog_evidence,
)
from butlers.core.dashboard_fast_answer_policy import (
    FAST_ANSWER_CONCIERGE_TOOLS_V1,
    FastAnswerAdmission,
    ReadGrant,
    admit_fast_answer,
    reply_observation_seconds,
    valid_grants,
)

pytestmark = pytest.mark.unit


def _row(owner="concierge", score=0.02):
    return {
        "id": "catalog-one",
        "source_butler": owner,
        "source_schema": owner,
        "source_table": "sessions",
        "source_id": "session-one",
        "rrf_score": score,
        "semantic_rank": 1,
        "keyword_rank": 2,
        "canonical_content": "withheld",
        "sensitivity": "normal",
    }


def _admission(**changes):
    data = {
        "lane": "D",
        "scope": "system",
        "selected_owner": "concierge",
        "question": "How is the fleet?",
        "reads": [{"name": "dashboard_read_fleet_status"}],
    }
    data.update(changes)
    return FastAnswerAdmission.model_validate_json(json.dumps(data))


def test_catalog_whole_population_and_deterministic_owner_rule():
    # REQ-dashboard-fast-answer-003: poison every malformed position, retain provenance only.
    eligible = frozenset({"concierge"})
    good = _row()
    one = validate_catalog_evidence([good], eligible_owners=eligible)
    assert one.selected_owner == "concierge"
    # This fixed labeled seed tests selection mechanics, not live search quality.
    corpus = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "fast_answer_catalog_corpus.json").read_text()
    )
    selected, wrong = 0, 0
    for case in corpus:
        observed = validate_catalog_evidence(case["rows"], eligible_owners=eligible).selected_owner
        selected += observed is not None
        wrong += observed is not None and observed != case["expected_owner"]
        assert observed == case["expected_owner"], case["label"]
    assert selected > 0 and wrong == 0
    assert set(one.candidates[0].provenance()) == {
        "catalog_id",
        "owner",
        "source_schema",
        "source_table",
        "source_id",
        "ranking_method",
        "rrf_score",
        "semantic_rank",
        "keyword_rank",
    }
    assert validate_catalog_evidence([], eligible_owners=eligible).state == "no_match"
    for bad in (
        None,
        {},
        _row(score=float("nan")),
        _row(score=True),
        {**good, "semantic_rank": None},
        {**good, "source_id": ""},
    ):
        for rows in ([bad], [bad, good], [good, bad]):
            assert validate_catalog_evidence(rows, eligible_owners=eligible).state == "unavailable"
    assert validate_catalog_evidence([good] * 4, eligible_owners=eligible).state == "unavailable"
    disagreement = validate_catalog_evidence(
        [good, _row("finance", 0.001)], eligible_owners=eligible
    )
    assert disagreement.selected_owner is None
    assert len(disagreement.candidates) == 2
    tied = validate_catalog_evidence([good, good, _row("finance")], eligible_owners=eligible)
    assert tied.selected_owner is None
    lower = validate_catalog_evidence(
        [good, good, _row("finance", 0.001)], eligible_owners=eligible
    )
    assert lower.selected_owner == "concierge"
    assert (
        validate_catalog_evidence([_row("finance")], eligible_owners=eligible).selected_owner
        is None
    )


@pytest.mark.asyncio
async def test_catalog_owning_lifecycle_unavailability_and_replacement(monkeypatch):
    # REQ-dashboard-fast-answer-003: missing/degraded authority is never empty success.
    calls = []

    async def old(query):
        calls.append(("old", query))
        return [_row()]

    async def replacement(query):
        calls.append(("replacement", query))
        return []

    async def degraded(query):
        raise RuntimeError("private operand must never escape")

    owner = "fast-policy-test"
    eligible = frozenset({"concierge"})
    assert (
        await resolve_fast_answer_catalog("q", butler_name=owner, eligible_owners=eligible)
    ).state == "unavailable"
    memory_hooks.register_fast_answer_catalog(owner, old)
    try:
        assert (
            await resolve_fast_answer_catalog("q", butler_name=owner, eligible_owners=eligible)
        ).selected_owner == "concierge"
        assert (
            await resolve_fast_answer_catalog(
                "q", butler_name="other-owner", eligible_owners=eligible
            )
        ).state == "unavailable"
        memory_hooks.register_fast_answer_catalog(owner, replacement)
        memory_hooks.unregister_fast_answer_catalog(owner, old)
        assert (
            await resolve_fast_answer_catalog("q", butler_name=owner, eligible_owners=eligible)
        ).state == "no_match"
        memory_hooks.register_fast_answer_catalog(owner, degraded)
        result = await memory_hooks.search_fast_answer_catalog(owner, "q")
        assert result == {"status": "unavailable", "reason": "catalog_read_unavailable"}
    finally:
        memory_hooks.unregister_fast_answer_catalog(owner, degraded)
    assert calls == [("old", "q"), ("replacement", "q")]

    # Exercise the actual started module producer, held configuration and pool.
    from butlers.modules.memory import MemoryModule
    from butlers.modules.memory.tools._helpers import _search

    module = MemoryModule()
    pool = SimpleNamespace(fetchval=AsyncMock(return_value=None))
    accessor = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(catalog_read_sensitivity="normal"))
    )
    db = SimpleNamespace(schema=owner, pool=pool, runtime_config_accessor=accessor)
    search = AsyncMock(return_value=[_row()])
    engine = object()
    monkeypatch.setattr(_search, "search_catalog", search)
    monkeypatch.setattr(module, "_get_embedding_engine", lambda: engine)
    monkeypatch.setattr(module, "_register_default_maintenance_schedules", AsyncMock())
    await module.on_startup(config=None, db=db)
    try:
        assert (
            await resolve_fast_answer_catalog("q", butler_name=owner, eligible_owners=eligible)
        ).selected_owner == "concierge"
        assert search.await_args.args == (pool, "q", engine)
        assert search.await_args.kwargs["limit"] == 3
        assert search.await_args.kwargs["mode"] == "hybrid"
        assert search.await_args.kwargs["read_policy"] == _search.resolve_catalog_read_policy(
            "normal"
        )
        db.runtime_config_accessor = None
        assert (
            await resolve_fast_answer_catalog("q", butler_name=owner, eligible_owners=eligible)
        ).state == "unavailable"
        assert search.await_count == 1
        db.runtime_config_accessor = accessor
        accessor.get.side_effect = RuntimeError("private configuration operand")
        assert await memory_hooks.search_fast_answer_catalog(owner, "q") == {
            "status": "unavailable",
            "reason": "catalog_read_unavailable",
        }
        assert search.await_count == 1
        accessor.get.side_effect = None
        assert (
            await resolve_fast_answer_catalog("q", butler_name=owner, eligible_owners=eligible)
        ).selected_owner == "concierge"
        assert search.await_count == 2
    finally:
        await module.on_shutdown()
    assert (
        await resolve_fast_answer_catalog("q", butler_name=owner, eligible_owners=eligible)
    ).state == "unavailable"


def test_exact_grant_admission_preserves_domain_and_terminal_boundaries():
    # REQ-dashboard-fast-answer-001 / REQ-dashboard-fast-answer-004.
    expected = {
        "dashboard_read_fleet_status",
        "dashboard_read_butler_detail",
        "dashboard_read_sessions_recent",
        "dashboard_read_session_detail",
        "dashboard_read_sessions_aggregate",
        "dashboard_read_sessions_trigger_breakdown",
        "dashboard_read_fleet_errors_recent",
        "dashboard_read_fleet_search",
        "dashboard_read_timeline_recent",
        "dashboard_read_butler_activity",
        "dashboard_read_spend_summary",
        "dashboard_read_spend_daily",
        "dashboard_read_spend_top_sessions",
        "dashboard_read_spend_breakdown_by_butler",
        "dashboard_read_spend_breakdown_by_model",
        "dashboard_read_insight_delivery_state",
    }
    assert set(FAST_ANSWER_CONCIERGE_TOOLS_V1) == expected
    catalog = validate_catalog_evidence([_row()], eligible_owners=frozenset({"concierge"}))

    def decide(admission, **options):
        return admit_fast_answer(
            admission, catalog, registered_tools=frozenset(expected), module_enabled=True, **options
        )

    assert decide(_admission()).state == "eligible"
    assert decide(_admission(scope="domain")).state == "fallback"
    for lane in ("A", "B", "C", "ambiguous"):
        assert decide(_admission(lane=lane)).state == "fallback"
    assert decide(_admission(reads=[{"name": "conversation_reply"}])).state == "fallback"
    assert decide(_admission(reads=[{"name": "dashboard_read_future"}])).state == "fallback"
    assert decide(_admission(selected_owner="finance")).state == "fallback"
    for bad in (
        None,
        {},
        {**FAST_ANSWER_CONCIERGE_TOOLS_V1, "future": ReadGrant()},
        {
            **FAST_ANSWER_CONCIERGE_TOOLS_V1,
            "dashboard_read_fleet_status": replace(ReadGrant(), effect="write"),
        },
    ):
        assert not valid_grants(bad)
        assert decide(_admission(), grants=bad).state == "fallback"
    assert (
        admit_fast_answer(
            _admission(), catalog, registered_tools=frozenset(), module_enabled=True
        ).state
        == "fallback"
    )
    assert (
        admit_fast_answer(
            _admission(), catalog, registered_tools=frozenset(expected), module_enabled=False
        ).state
        == "fallback"
    )
    with pytest.raises(ValidationError):
        _admission(reads=[{"name": "dashboard_read_fleet_status"}] * 4)
    with pytest.raises(ValidationError):
        _admission(write=True)


def test_empty_catalog_declines_only_after_system_question_and_observation_is_separate():
    # REQ-dashboard-fast-answer-003 / REQ-dashboard-fast-answer-005.
    for lane in ("A", "B", "C", "ambiguous"):
        assert (
            admit_fast_answer(
                _admission(lane=lane),
                CatalogOutcome("no_match"),
                registered_tools=frozenset(),
                module_enabled=True,
            ).state
            == "fallback"
        )
    assert (
        admit_fast_answer(
            _admission(),
            CatalogOutcome("no_match"),
            registered_tools=frozenset(),
            module_enabled=True,
        ).state
        == "no_match"
    )
    assert (
        admit_fast_answer(
            _admission(),
            CatalogOutcome("unavailable"),
            registered_tools=frozenset(),
            module_enabled=True,
        ).state
        == "fallback"
    )
    assert reply_observation_seconds("answer") == 45
    for lane in (None, "non_answer", "unknown", "route"):
        assert reply_observation_seconds(lane) == 300
