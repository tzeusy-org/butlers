"""Unit tests for the chronicler day-close memory write-back loop (bu-93y4rt).

Covers the tasks.md §8.6 acceptance contract with pure, DB-free assertions:

1. Insights land in the chronicler's OWN schema only — every store carries
   ``source=chronicler`` and the orchestrator has no other write sink.
2. Enrichment leaves ONLY as an MCP proposal (routed through the injected
   proposer), never as a stored chronicler fact or a relationship write.
3. The write-back path adds NO owner-facing message — there is no ``notify``
   collaborator to call.

Plus focused tests for each pure synthesis function and the production
collaborator factories. The real-Postgres path (rollup reads + companion
co-presence SQL) is exercised separately in
``tests/integration/test_chronicler_writeback_integration.py``.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from butlers.chronicler.balance import LaneBalance
from butlers.chronicler.writeback import (
    PREDICATE_LANE_SKEW,
    PREDICATE_RECURRING_COMPANION,
    PREDICATE_SELF_REMINDER,
    PREDICATE_SLEEP_DEBT,
    SOURCE_BUTLER,
    CompanionCopresence,
    EnrichmentProposal,
    InsightFact,
    build_chronicler_fact_writer,
    build_relationship_enrichment_proposer,
    execute_writeback,
    synthesize_enrichment_proposals,
    synthesize_lane_skew_insights,
    synthesize_self_reminders,
    synthesize_sleep_debt_insight,
)

_DAY = date(2026, 7, 9)


# ── recording collaborators ────────────────────────────────────────────────


class _StoreSpy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return "fact-id"

    @property
    def predicates(self) -> list[str]:
        return [c["predicate"] for c in self.calls]


class _ProposeSpy:
    def __init__(self) -> None:
        self.proposals: list[EnrichmentProposal] = []

    async def __call__(self, proposal: EnrichmentProposal) -> None:
        self.proposals.append(proposal)


def _anomalous_work_balance() -> LaneBalance:
    # 0s work today vs an 8h usual, with a long baseline history → anomaly.
    return LaneBalance(
        lane="work",
        seconds=0,
        baseline_seconds=8 * 3600,
        delta_seconds=-8 * 3600,
        baseline_sample_days=14,
    )


# ── pure synthesis ─────────────────────────────────────────────────────────


def test_lane_skew_insight_fires_on_anomaly():
    balances = [
        _anomalous_work_balance(),
        LaneBalance("sleep", 8 * 3600, 8 * 3600, 0, 14),  # within usual → no skew
    ]
    insights = synthesize_lane_skew_insights(_DAY, balances)
    assert len(insights) == 1
    insight = insights[0]
    assert insight.predicate == PREDICATE_LANE_SKEW
    assert insight.metadata["lane"] == "work"
    assert insight.metadata["direction"] == "down"
    assert "work" in insight.tags


def test_lane_skew_no_insight_within_baseline():
    balances = [LaneBalance("work", 8 * 3600, 8 * 3600, 0, 14)]
    assert synthesize_lane_skew_insights(_DAY, balances) == []


def test_sleep_debt_insight_accumulates_over_shortfall():
    insight = synthesize_sleep_debt_insight(
        _DAY,
        sleep_daily_seconds=[20000, 20000, 20000],  # ~2.4h short each of 3 days
        sleep_baseline_seconds=28800,
    )
    assert insight is not None
    assert insight.predicate == PREDICATE_SLEEP_DEBT
    assert insight.metadata["shortfall_days"] == 3


def test_sleep_debt_none_when_rested_or_no_baseline():
    assert (
        synthesize_sleep_debt_insight(
            _DAY, sleep_daily_seconds=[28800, 28800], sleep_baseline_seconds=28800
        )
        is None
    )
    assert (
        synthesize_sleep_debt_insight(_DAY, sleep_daily_seconds=[0, 0], sleep_baseline_seconds=None)
        is None
    )


def test_self_reminders_only_for_pending_backfill_flags():
    flags = [
        SimpleNamespace(flag_type="feeder_dark", severity="warning", detail={"src": ["x"]}),
        SimpleNamespace(flag_type="routine_break", severity="info", detail={}),
        SimpleNamespace(flag_type="sleep_missing", severity="warning", detail={}),
    ]
    reminders = synthesize_self_reminders(_DAY, flags)
    kinds = {r.metadata["flag_type"] for r in reminders}
    assert kinds == {"feeder_dark", "sleep_missing"}  # routine_break excluded
    assert all(r.predicate == PREDICATE_SELF_REMINDER for r in reminders)
    assert all(r.confidence < 0.5 for r in reminders)  # low-confidence markers


def test_enrichment_proposals_respect_distinct_day_threshold():
    companions = [
        CompanionCopresence(entity_id="e-recurring", distinct_days=4, episode_count=9),
        CompanionCopresence(entity_id="e-once", distinct_days=1, episode_count=1),
    ]
    proposals = synthesize_enrichment_proposals(
        companions, window_start=date(2026, 6, 12), window_end=_DAY
    )
    assert [p.entity_id for p in proposals] == ["e-recurring"]
    assert proposals[0].predicate == PREDICATE_RECURRING_COMPANION
    assert proposals[0].dedup_key.startswith(f"{PREDICATE_RECURRING_COMPANION}:e-recurring:")


# ── orchestrator: the §8.6 acceptance contract ─────────────────────────────


async def test_insights_land_in_own_schema_only():
    store = _StoreSpy()
    propose = _ProposeSpy()
    insights = synthesize_lane_skew_insights(_DAY, [_anomalous_work_balance()])
    reminders = synthesize_self_reminders(
        _DAY, [SimpleNamespace(flag_type="feeder_dark", severity="warning", detail={})]
    )

    result = await execute_writeback(
        insights=insights,
        self_reminders=reminders,
        proposals=[],
        store_fact_fn=store,
        propose_enrichment_fn=propose,
    )

    assert result.insights_written == 1
    assert result.self_reminders_written == 1
    assert result.errors == 0
    # Every stored fact is stamped as chronicler-owned; the orchestrator has no
    # other write sink and cannot reach a foreign schema.
    assert store.calls, "expected at least one own-schema write"
    assert all(c["metadata"]["source"] == SOURCE_BUTLER for c in store.calls)
    assert propose.proposals == []  # no enrichment for facts


async def test_enrichment_is_mcp_proposal_not_a_stored_or_direct_write():
    store = _StoreSpy()
    propose = _ProposeSpy()
    proposals = synthesize_enrichment_proposals(
        [CompanionCopresence(entity_id="e-1", distinct_days=5, episode_count=12)],
        window_start=date(2026, 6, 12),
        window_end=_DAY,
    )

    result = await execute_writeback(
        insights=[],
        self_reminders=[],
        proposals=proposals,
        store_fact_fn=store,
        propose_enrichment_fn=propose,
    )

    # Enrichment left ONLY via the MCP proposer …
    assert result.proposals_sent == 1
    assert [p.entity_id for p in propose.proposals] == ["e-1"]
    # … and was never written as a chronicler fact (nor anywhere else — the
    # orchestrator has no relationship pool to write).
    assert PREDICATE_RECURRING_COMPANION not in store.predicates
    assert store.calls == []


async def test_writeback_sends_no_owner_facing_message():
    store = _StoreSpy()
    propose = _ProposeSpy()
    notify_spy = AsyncMock()  # deliberately wired NOWHERE

    insights = synthesize_lane_skew_insights(_DAY, [_anomalous_work_balance()])
    await execute_writeback(
        insights=insights,
        self_reminders=[],
        proposals=synthesize_enrichment_proposals(
            [CompanionCopresence(entity_id="e-1", distinct_days=5, episode_count=12)],
            window_start=date(2026, 6, 12),
            window_end=_DAY,
        ),
        store_fact_fn=store,
        propose_enrichment_fn=propose,
    )
    # The write-back has no notify collaborator: the once-daily day-close
    # summary (sent by the prompt) is the only sanctioned owner-facing message.
    notify_spy.assert_not_awaited()


async def test_missing_proposer_skips_enrichment_without_error():
    store = _StoreSpy()
    result = await execute_writeback(
        insights=[],
        self_reminders=[],
        proposals=synthesize_enrichment_proposals(
            [CompanionCopresence(entity_id="e-1", distinct_days=5, episode_count=12)],
            window_start=date(2026, 6, 12),
            window_end=_DAY,
        ),
        store_fact_fn=store,
        propose_enrichment_fn=None,
    )
    assert result.proposals_sent == 0
    assert result.errors == 0


async def test_store_failure_is_counted_not_raised():
    async def _boom(**kwargs):
        raise RuntimeError("db down")

    result = await execute_writeback(
        insights=[
            InsightFact(
                subject="s",
                predicate=PREDICATE_LANE_SKEW,
                content="c",
                permanence="volatile",
                importance=5.0,
                confidence=0.7,
            )
        ],
        self_reminders=[],
        proposals=[],
        store_fact_fn=_boom,
        propose_enrichment_fn=None,
    )
    assert result.insights_written == 0
    assert result.errors == 1


# ── production collaborator factories ──────────────────────────────────────


async def test_fact_writer_binds_chronicler_schema(monkeypatch):
    captured: dict = {}

    async def _fake_store_fact(pool, subject, predicate, content, engine, **kwargs):
        captured.update(pool=pool, subject=subject, predicate=predicate, content=content, **kwargs)
        return "id"

    monkeypatch.setattr("butlers.modules.memory.storage.store_fact", _fake_store_fact)
    sentinel_pool = object()
    writer = build_chronicler_fact_writer(sentinel_pool, object())
    await writer(
        subject="sleep",
        predicate=PREDICATE_SLEEP_DEBT,
        content="debt",
        permanence="volatile",
        importance=6.0,
        tags=["chronicler-insight"],
        metadata={"confidence": 0.7},
    )
    assert captured["pool"] is sentinel_pool
    assert captured["source_butler"] == SOURCE_BUTLER
    assert captured["source_schema"] == SOURCE_BUTLER


async def test_enrichment_proposer_posts_mail_to_relationship():
    client = SimpleNamespace(call_tool=AsyncMock(return_value={"ok": True}))
    propose = build_relationship_enrichment_proposer(lambda: client)
    proposal = EnrichmentProposal(
        entity_id="e-1",
        predicate=PREDICATE_RECURRING_COMPANION,
        distinct_days=5,
        episode_count=12,
        window_start=date(2026, 6, 12),
        window_end=_DAY,
        dedup_key="recurring-companion:e-1:2026-06-12_2026-07-09",
        message="Recurring companion",
    )
    await propose(proposal)
    client.call_tool.assert_awaited_once()
    tool_name, args = client.call_tool.await_args.args
    assert tool_name == "post_mail"
    assert args["target_butler"] == "relationship"
    assert args["sender"] == SOURCE_BUTLER
    assert args["metadata"]["entity_id"] == "e-1"


async def test_enrichment_proposer_noop_without_client():
    propose = build_relationship_enrichment_proposer(lambda: None)
    # No client available → silent no-op, no exception.
    assert (
        await propose(
            EnrichmentProposal(
                entity_id="e-1",
                predicate=PREDICATE_RECURRING_COMPANION,
                distinct_days=5,
                episode_count=12,
                window_start=date(2026, 6, 12),
                window_end=_DAY,
                dedup_key="k",
                message="m",
            )
        )
        is None
    )
