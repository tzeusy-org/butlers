"""``context.search`` through the goal runner and the broker's ledger (RFC 0038 AC8)."""

from __future__ import annotations

import asyncio
import shutil
from unittest.mock import patch
from uuid import UUID

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from butlers.capability_broker.contracts import Catalog, Reason
from butlers.core.goal_labels import Label
from butlers.goal_runner.context_search import (
    ContextQuery,
    ContextSearch,
    ContextSearchLimits,
    SourceHit,
    SourceResult,
)
from butlers.goal_runner.tools import GoalTools
from tests.capability_broker.helpers import capability
from tests.capability_broker.test_broker_ledger import (
    RULES,
    SEARCH,
    StaticCatalog,
    _grant,
)
from tests.goal_runner.test_goal_runner import harness, refused

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

# Two searchable domains beside the broker test domain.
MAIL_READ = capability("read_mail", "read", "mail")
HEALTH_READ = capability("recall", "read", "health", output_trust="stamped")
MAIL_SEND = capability("send", "communicate", "mail")
SHADOW = capability("search", "read", "context")  # a domain that tries to take the namespace
CATALOG = Catalog("catalog-v1", (SEARCH, RULES, MAIL_READ, HEALTH_READ, MAIL_SEND, SHADOW))


def hit(ref: str, **kwargs) -> SourceHit:
    return SourceHit(ref, "notes", "fact", f"excerpt {ref}", **kwargs)


class Source:
    """A domain's search seam: records every query it is asked, answers a fixed result."""

    def __init__(self, *hits: SourceHit, owner_untainted: bool = True, delay: float = 0.0):
        self.result = SourceResult(hits, stamped_owner_untainted=owner_untainted)
        self.delay, self.queries = delay, []
        self.failure: Exception | None = None

    async def search(self, query: ContextQuery) -> SourceResult:
        self.queries.append(query)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failure is not None:
            raise self.failure
        return self.result


@pytest.fixture
def sources():
    return {"mail": Source(hit("m1")), "health": Source(hit("h1"), hit("h2"))}


async def configure(h, sources, **limits):
    h.w.broker.catalog = StaticCatalog(CATALOG)
    h.tools = GoalTools(
        h.w.broker, h.runner, ContextSearch(h.w.broker, sources, ContextSearchLimits(**limits))
    )


async def counts(h) -> dict[str, int]:
    tables = ("effects", "goal_handles", "goal_outcomes", "goals", "invocations", "budgets")
    return {t: await h.w.owner.fetchval(f"SELECT count(*) FROM switchboard.{t}") for t in tables}


async def used(h, goal: UUID, invocation: UUID) -> tuple[int, int]:
    goal_used = await h.w.owner.fetchval(
        "SELECT used_calls FROM switchboard.budgets WHERE goal_id = $1", goal
    )
    own = await h.w.owner.fetchval(
        "SELECT used_calls FROM switchboard.invocations WHERE id = $1", invocation
    )
    return goal_used, own


async def exposed(h, invocation: UUID) -> bool:
    row = await h.row(invocation)
    return "world" in row["label_instr"] or "world" in row["label_data"]


SEARCHING = "context__search"


async def test_the_tool_is_listed_to_both_depths_only_when_configured(broker_db_url, sources):
    async with harness(broker_db_url) as h:
        assert SEARCHING not in [t.name for t in h.tools.listed(0)]
        assert SEARCHING not in [t.name for t in h.tools.listed(1)]
        await configure(h, sources)
        assert SEARCHING in [t.name for t in h.tools.listed(0)]
        assert [t.name for t in h.tools.listed(1)] == ["goal__return", SEARCHING]


async def test_an_unconfigured_tool_is_not_callable(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        reason = await refused(h.call(goal, lead.token, SEARCHING, {"query": "x"}))
        assert reason is Reason.CAPABILITY


async def test_a_lead_searches_its_granted_domains_and_gets_whole_provenanced_items(
    broker_db_url, sources
):
    async with harness(broker_db_url) as h:
        await configure(h, sources)
        goal = await h.goal(_grant(MAIL_READ), _grant(HEALTH_READ))
        lead = await h.lead(goal)
        result = await h.call(goal, lead.token, SEARCHING, {"query": "dentist", "k": 5})
        assert result["status"] == "complete"
        assert result["sources"] == {"health": "ok", "mail": "ok"}
        assert {e["ref"] for e in result["hits"]} == {"mail:m1", "health:h1", "health:h2"}
        assert all(
            {"source", "region", "kind", "excerpt", "authority", "observed_at"} <= set(e)
            for e in result["hits"]
        )
        assert result["omitted"] == {"count": 0, "reason": None}


async def test_a_source_sees_only_the_closed_query_never_the_caller(broker_db_url, sources):
    async with harness(broker_db_url) as h:
        await configure(h, sources)
        goal = await h.goal(_grant(MAIL_READ))
        lead = await h.lead(goal)
        await h.call(goal, lead.token, SEARCHING, {"query": "dentist"})
        (query,) = sources["mail"].queries
        assert isinstance(query, ContextQuery) and query.query == "dentist"
        assert lead.token not in repr(query) and str(goal) not in repr(query)


async def test_the_call_is_charged_to_the_goal_and_the_invocation_and_writes_no_effect(
    broker_db_url, sources
):
    async with harness(broker_db_url) as h:
        await configure(h, sources)
        goal = await h.goal(_grant(MAIL_READ), _grant(HEALTH_READ))
        lead = await h.lead(goal)
        before_used = await used(h, goal, lead.invocation_id)
        before = await counts(h)
        await h.call(goal, lead.token, SEARCHING, {"query": "dentist"})
        after_used = await used(h, goal, lead.invocation_id)
        assert after_used == (before_used[0] + 1, before_used[1] + 1)
        assert await counts(h) == before  # no effect, handle, outcome or new row anywhere


async def test_a_search_only_changes_rows_in_budgets_and_invocations(broker_db_url):
    async with harness(broker_db_url) as h:
        # A source that reads no sensitivity category joins nothing into the label.
        neutral = Source(hit("m1"))
        neutral.result = SourceResult(neutral.result.hits, True, (), frozenset())
        await configure(h, {"mail": neutral})
        goal = await h.goal(_grant(MAIL_READ))
        lead = await h.lead(goal)

        async def snapshot():
            rows = {}
            for table in ("goals", "effects", "goal_handles", "goal_outcomes", "refusals"):
                try:
                    rows[table] = [
                        dict(r) for r in await h.w.owner.fetch(f"SELECT * FROM switchboard.{table}")
                    ]
                except Exception:  # a table this schema does not have
                    rows[table] = None
            return rows

        before = await snapshot()
        await h.call(goal, lead.token, SEARCHING, {"query": "dentist"})
        after = await snapshot()
        changed = {
            (t, k)
            for t in before
            for old, new in zip(before[t] or [], after[t] or [], strict=False)
            for k in old
            if old[k] != new[k]
        }
        assert not changed, changed
        assert after == before


async def test_an_exhausted_goal_budget_refuses_budget_without_asking_the_source(
    broker_db_url, sources
):
    async with harness(broker_db_url) as h:
        await configure(h, sources)
        goal = await h.w.goal(_grant(MAIL_READ), max_calls=1)
        lead = await h.lead(goal)
        await h.call(goal, lead.token, SEARCHING, {"query": "one"})
        asked = len(sources["mail"].queries)
        assert await refused(h.call(goal, lead.token, SEARCHING, {"query": "two"})) is Reason.BUDGET
        assert len(sources["mail"].queries) == asked
        assert await h.goal_status(goal) == "budget_exhausted"


async def test_an_exhausted_child_share_refuses_budget_and_the_goal_runs_on(broker_db_url, sources):
    async with harness(broker_db_url) as h:
        await configure(h, sources)
        goal = await h.goal(_grant(MAIL_READ))
        lead = await h.lead(goal)
        child = await h.delegate(goal, lead.token, MAIL_READ.identity, calls=1)
        launch = await h.launched(2)
        assert launch.invocation_id == child
        await h.call(goal, launch.token, SEARCHING, {"query": "one"})
        asked = len(sources["mail"].queries)
        reason = await refused(h.call(goal, launch.token, SEARCHING, {"query": "two"}))
        assert reason is Reason.BUDGET
        assert len(sources["mail"].queries) == asked
        assert await h.goal_status(goal) == "running"


async def test_a_child_sees_only_the_sources_its_grant_covers(broker_db_url, sources):
    async with harness(broker_db_url) as h:
        await configure(h, sources)
        goal = await h.goal(_grant(MAIL_READ), _grant(HEALTH_READ))
        lead = await h.lead(goal)
        await h.delegate(goal, lead.token, HEALTH_READ.identity)
        launch = await h.launched(2)
        result = await h.call(goal, launch.token, SEARCHING, {"query": "dentist"})
        assert result["sources"] == {"health": "ok"}
        assert {e["source"] for e in result["hits"]} == {"health"}
        assert sources["mail"].queries == []  # never even asked


@pytest.mark.parametrize(
    "grants",
    [
        pytest.param((), id="no grant at all"),
        pytest.param((RULES,), id="administer is not a source"),
        pytest.param((MAIL_SEND,), id="communicate is not a source"),
        pytest.param((SEARCH,), id="a domain with no registered source"),
    ],
)
async def test_a_grant_that_covers_no_source_refuses_capability_and_charges_nothing(
    broker_db_url, sources, grants
):
    async with harness(broker_db_url) as h:
        await configure(h, sources)
        goal = await h.goal(*(_grant(c) for c in grants), _grant(MAIL_SEND))
        lead = await h.lead(goal)
        before = await used(h, goal, lead.invocation_id)
        reason = await refused(h.call(goal, lead.token, SEARCHING, {"query": "x"}))
        assert reason is Reason.CAPABILITY
        assert await used(h, goal, lead.invocation_id) == before
        assert all(s.queries == [] for s in sources.values())


async def test_a_resource_restricted_read_grant_does_not_open_its_domain(broker_db_url):
    restricted = capability("read_box", "read", "mail", resource_params=("box",))
    async with harness(broker_db_url) as h:
        await configure(h, {"mail": Source(hit("m1"))})
        h.w.broker.catalog = StaticCatalog(Catalog("catalog-v1", (restricted,)))
        goal = await h.goal(_grant(restricted, resources=(("box", ("inbox",)),)))
        lead = await h.lead(goal)
        reason = await refused(h.call(goal, lead.token, SEARCHING, {"query": "x"}))
        assert reason is Reason.CAPABILITY


async def test_a_bad_query_refuses_policy_before_charging_or_asking_a_source(
    broker_db_url, sources
):
    async with harness(broker_db_url) as h:
        await configure(h, sources)
        goal = await h.goal(_grant(MAIL_READ))
        lead = await h.lead(goal)
        before = await used(h, goal, lead.invocation_id)
        for bad in ({}, {"query": "x", "grants": []}, {"query": "x", "k": 99}):
            assert await refused(h.call(goal, lead.token, SEARCHING, bad)) is Reason.POLICY
        assert await used(h, goal, lead.invocation_id) == before
        assert sources["mail"].queries == []


async def test_a_domain_cannot_take_the_reserved_context_namespace(broker_db_url, sources):
    async with harness(broker_db_url) as h:
        await configure(h, sources)
        goal = await h.goal(_grant(SHADOW))
        lead = await h.lead(goal)
        assert SHADOW.identity not in [
            c.identity for c in await h.w.broker.list_tools(goal, lead.token)
        ]
        reason = await refused(h.w.broker.call(goal, lead.token, SHADOW.identity, {}))
        assert reason is Reason.CAPABILITY


async def test_owner_untainted_hits_leave_the_reader_clean(broker_db_url, sources):
    async with harness(broker_db_url) as h:
        await configure(h, sources)
        goal = await h.goal(_grant(MAIL_READ))
        lead = await h.lead(goal)
        await h.call(goal, lead.token, SEARCHING, {"query": "x"})
        assert not await exposed(h, lead.invocation_id)


async def test_hits_without_a_stamp_account_expose_the_reader(broker_db_url):
    async with harness(broker_db_url) as h:
        await configure(h, {"mail": Source(hit("m1"), owner_untainted=False)})
        goal = await h.goal(_grant(MAIL_READ))
        lead = await h.lead(goal)
        await h.call(goal, lead.token, SEARCHING, {"query": "x"})
        assert await exposed(h, lead.invocation_id)


async def test_a_listed_tainted_writer_exposes_the_reader(broker_db_url):
    async with harness(broker_db_url) as h:
        await configure(h, {})
        goal = await h.goal(_grant(MAIL_READ))
        lead = await h.lead(goal)
        # A writer invocation that read something third-party: its label is exposed.
        writer, _ = await h.w.invocation(
            goal, _grant(MAIL_READ), depth=1, parent=lead.invocation_id
        )
        async with h.w.broker._transaction() as ledger:
            await ledger._join(goal, writer, Label.unknown(), "read")
        source = Source(hit("m1"))
        source.result = SourceResult(
            source.result.hits, stamped_owner_untainted=True, stamped_writers=((goal, writer),)
        )
        h.tools = GoalTools(h.w.broker, h.runner, ContextSearch(h.w.broker, {"mail": source}))
        await h.call(goal, lead.token, SEARCHING, {"query": "x"})
        assert await exposed(h, lead.invocation_id)


async def test_the_categories_a_source_read_join_the_reader(broker_db_url):
    async with harness(broker_db_url) as h:
        source = Source(hit("h1"))
        source.result = SourceResult(source.result.hits, True, (), frozenset({"health"}))
        await configure(h, {"health": source})
        goal = await h.goal(_grant(HEALTH_READ))
        lead = await h.lead(goal)
        await h.call(goal, lead.token, SEARCHING, {"query": "x"})
        assert "health" in (await h.row(lead.invocation_id))["label_sens"]
        assert not await exposed(h, lead.invocation_id)


async def test_a_source_that_found_nothing_teaches_the_reader_nothing(broker_db_url):
    async with harness(broker_db_url) as h:
        await configure(h, {"mail": Source(owner_untainted=False)})
        goal = await h.goal(_grant(MAIL_READ))
        lead = await h.lead(goal)
        result = await h.call(goal, lead.token, SEARCHING, {"query": "x"})
        assert result["hits"] == [] and result["status"] == "complete"
        assert not await exposed(h, lead.invocation_id)


async def test_a_slow_or_failing_source_degrades_and_the_rest_still_answer(broker_db_url, sources):
    async with harness(broker_db_url) as h:
        sources["mail"].delay = 5
        sources["health"].failure = None
        await configure(h, sources, per_domain_s=0.05, total_s=0.5)
        goal = await h.goal(_grant(MAIL_READ), _grant(HEALTH_READ))
        lead = await h.lead(goal)
        result = await h.call(goal, lead.token, SEARCHING, {"query": "x"})
        assert result["status"] == "degraded"
        assert result["sources"] == {"health": "ok", "mail": "timeout"}
        assert {e["source"] for e in result["hits"]} == {"health"}


async def test_every_source_failing_is_unavailable_not_an_empty_success(broker_db_url, sources):
    async with harness(broker_db_url) as h:
        sources["mail"].failure = RuntimeError("secret detail")
        sources["health"].failure = ValueError("more")
        await configure(h, sources)
        goal = await h.goal(_grant(MAIL_READ), _grant(HEALTH_READ))
        lead = await h.lead(goal)
        reason = await refused(h.call(goal, lead.token, SEARCHING, {"query": "x"}))
        assert reason is Reason.UNAVAILABLE


async def test_a_source_returning_garbage_is_failed_not_trusted(broker_db_url, sources):
    async with harness(broker_db_url) as h:
        sources["mail"].result = ["not", "a", "result"]
        await configure(h, sources)
        goal = await h.goal(_grant(MAIL_READ), _grant(HEALTH_READ))
        lead = await h.lead(goal)
        result = await h.call(goal, lead.token, SEARCHING, {"query": "x"})
        assert result["sources"] == {"health": "ok", "mail": "failed"}


async def test_a_stopped_goal_gets_no_results(broker_db_url, sources):
    async with harness(broker_db_url) as h:
        await configure(h, sources)
        goal = await h.goal(_grant(MAIL_READ))
        lead = await h.lead(goal)
        await h.runner.stop(goal)
        reason = await refused(h.call(goal, lead.token, SEARCHING, {"query": "x"}))
        assert reason in (Reason.AUTHENTICATION, Reason.GOAL, Reason.EPOCH)


async def test_the_span_carries_identifiers_only(broker_db_url, sources, monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("butlers.goal_runner.context_search._tracer", provider.get_tracer("t"))
    async with harness(broker_db_url) as h:
        await configure(h, sources)
        goal = await h.goal(_grant(MAIL_READ))
        lead = await h.lead(goal)
        await h.call(goal, lead.token, SEARCHING, {"query": "private dentist question"})
    (span,) = [s for s in exporter.get_finished_spans() if s.name == "goal.context_search"]
    assert dict(span.attributes) == {
        "goal_id": str(goal),
        "invocation_id": str(lead.invocation_id),
        "depth": 0,
    }
    assert "dentist" not in repr(span.attributes) and "excerpt" not in repr(span.attributes)


async def test_the_endpoint_lists_the_tool_to_a_lead_and_a_child(broker_db_url, sources):
    from butlers.capability_broker.server import _GrantedTools

    async with harness(broker_db_url) as h:
        await configure(h, sources)
        goal = await h.goal(_grant(MAIL_READ))
        lead = await h.lead(goal)
        await h.delegate(goal, lead.token, MAIL_READ.identity)
        child = await h.launched(2)
        provider = _GrantedTools(h.w.broker, h.tools)
        for launch in (lead, child):
            with patch(
                "butlers.capability_broker.server._credentials",
                return_value=(goal, launch.token),
            ):
                names = {tool.name for tool in await provider._list_tools()}
            assert SEARCHING in names
