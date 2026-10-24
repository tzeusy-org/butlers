"""Dark source value controls, not proof of a durable/verified producer adapter.

REQ-capability-authorization-013, REQ-goal-orchestration-007.
"""

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from butlers.core.goal_admission import Grant
from butlers.core.model_routing import Complexity
from butlers.core.obligation_sources import ObligationSource, RuntimeCreationSnapshot
from butlers.core.obligations import DeclarationError, parse_declaration

pytestmark = pytest.mark.unit


def test_runtime_ceiling_refuses_capability_resource_and_park_only_widening():
    creator = RuntimeCreationSnapshot(
        UUID(int=1), (Grant("health__read", (("entity_id", ("a", "b")),), True),), True
    )
    declaration = parse_declaration(
        {
            "version": 1,
            "capabilities": [
                {"capability": "health__read", "resources": {"entity_id": ["a"]}, "park_only": True}
            ],
        }
    )
    source = ObligationSource("health", "schedule", UUID(int=2), "a" * 64, declaration, creator)
    assert source.creator.exposed is True
    for slot in ("nominal-due-1", "nominal-due-2"):
        # Exposure belongs to the immutable creation snapshot, not an occurrence
        # request that could clear it. This is not a broker authentication test.
        assert source.occurrence(slot).kind == "occurrence"
        assert source.creator.exposed
    for grant in (
        {"capability": "health__write"},
        {"capability": "health__read", "park_only": True},
        {"capability": "health__read", "resources": {"entity_id": ["c"]}, "park_only": True},
        {"capability": "health__read", "resources": {"entity_id": ["a"]}},
    ):
        wider = parse_declaration({"version": 1, "capabilities": [grant]})
        with pytest.raises(DeclarationError, match="creator_ceiling_exceeded"):
            replace(source, declaration=wider)


def test_source_revision_occurrence_and_domain_are_independent_replay_keys():
    declaration = parse_declaration({"version": 1, "capabilities": []})
    source = ObligationSource("education", "schedule", UUID(int=3), "b" * 64, declaration)
    first = source.occurrence("stored-nominal-due")
    assert first == source.occurrence("stored-nominal-due")
    variants = (
        replace(source, revision="c" * 64),
        replace(source, owner_domain="health"),
        replace(source, source_id=UUID(int=4)),
        replace(source, family="deadline"),
    )
    assert (
        len(
            {
                first.key,
                *(s.occurrence("stored-nominal-due").key for s in variants),
                source.occurrence("next-stored-due").key,
            }
        )
        == 6
    )
    with pytest.raises(DeclarationError):
        source.occurrence("")
    with pytest.raises(DeclarationError):
        replace(source, revision="caller-says-current")

    subscription = replace(source, family="subscription")
    with pytest.raises(DeclarationError):
        subscription.occurrence("event-body-cannot-be-a-slot")
    assert subscription.event(UUID(int=5), feed=True).kind == "feed"
    assert subscription.event(UUID(int=5), feed=False).kind == "domain"
    assert (
        subscription.event(UUID(int=5), feed=True).key
        != subscription.event(UUID(int=5), feed=False).key
    )
    assert (
        subscription.event(UUID(int=5), feed=True).key
        != subscription.event(UUID(int=6), feed=True).key
    )


class _LocalSchedulePool:
    """Closed entrypoint double, NOT evidence of PostgreSQL/CAS durability."""

    def __init__(self, row=None, *, claim=True, lookups=()):
        self.row = row
        self.claim = claim
        self.lookups = iter(lookups)
        self.calls = []
        self.claimed = False

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        if "information_schema.columns" in sql:
            return args[1] in {"task_type", "until_at", "max_token_budget"}
        return False

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        if "information_schema.columns" in sql:
            return [
                {"column_name": c}
                for c in args[1]
                if c in {"task_type", "max_token_budget", "until_at"}
            ]
        if "FROM scheduled_tasks" in sql and "next_run_at <= $1" in sql:
            return [self.row] if self.row else []
        return []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if "IS NOT DISTINCT FROM" in sql:
            assert args[-1] == self.row["next_run_at"]
            self.claimed = self.claim
            return {"id": self.row["id"]} if self.claim else None
        if "SELECT id, name, prompt" in sql:
            return next(self.lookups)
        return None

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "OK"


def _due_prompt(**updates):
    return {
        "id": UUID(int=101),
        "name": "private-task-name",
        "cron": "15 1 * * *",
        "dispatch_mode": "prompt",
        "prompt": "private-prompt-content",
        "job_name": None,
        "job_args": None,
        "complexity": None,
        "timezone": "UTC",
        "next_run_at": datetime(2026, 1, 1, tzinfo=UTC),
        "until_at": None,
        "max_token_budget": 123,
        **updates,
    }


def _closed_observer(monkeypatch):
    from opentelemetry import trace

    from butlers.core import obligation_admission as admission

    events, observations = [], []

    class RecordingSpan(trace.NonRecordingSpan):
        def add_event(self, name, attributes=None, timestamp=None):
            events.append((name, attributes))

    span = RecordingSpan(trace.INVALID_SPAN_CONTEXT)
    monkeypatch.setattr(
        "opentelemetry.trace.get_current_span",
        lambda *_, **__: span,
    )
    builder = getattr(admission, "build_dark_attempt_observation", None)
    if builder is not None:

        def recording_builder(**kwargs):
            result = builder(**kwargs)
            observations.append(result)
            return result

        monkeypatch.setattr(admission, "build_dark_attempt_observation", recording_builder)
    return admission, events, observations


def _assert_closed_events(events, phases):
    assert [a["phase"] for _, a in events] == phases
    assert all(
        name == "obligation.off_observation"
        and set(a) == {"mode", "kind", "phase", "authority", "definition_state", "quality"}
        and a["mode"] == "off"
        and a["authority"] == "unverified"
        and a["definition_state"] == "unavailable"
        and a["quality"] == "observed"
        for name, a in events
    )


async def test_off_cron_observes_actual_claim_and_original_slot_without_changing_dispatch(
    monkeypatch,
):
    from butlers.core import scheduler

    admission, events, observations = _closed_observer(monkeypatch)
    # Any B2 call from this OFF path is a real regression, not an authority stub.
    monkeypatch.setattr(admission, "run_source_transaction", AsyncMock(side_effect=AssertionError))
    monkeypatch.setattr(admission, "prepare_dark_submission", AsyncMock(side_effect=AssertionError))
    row = _due_prompt()
    pool = _LocalSchedulePool(row)
    result = {"status": "ok", "private": "original-result"}
    completion = AsyncMock()

    async def dispatch(**kwargs):
        assert pool.claimed
        assert kwargs == {
            "prompt": "bound-prompt",
            "trigger_source": "schedule:private-task-name",
            "complexity": Complexity.WORKHORSE,
            "max_token_budget": 123,
        }
        _assert_closed_events(events, ["post_claim"])
        return result

    count = await scheduler.tick(
        pool,
        dispatch,
        butler_name="health",
        prompt_hooks={row["name"]: lambda **_: "bound-prompt"},
        completion_hooks={row["name"]: completion},
    )
    assert count == 1
    assert len(observations) == 1
    observation = observations[0]
    assert observation.source_row_id == row["id"]
    assert observation.nominal_slot == row["next_run_at"]
    assert observation.owner_domain == "health" and observation.reported_event_id is None
    assert observation.kind == "schedule_attempt"
    assert completion.await_args.kwargs["result"] is result
    updates = [(sql, args) for sql, args in pool.calls if "SET last_run_at" in sql]
    assert len(updates) == 1 and updates[0][1][2] == result
    assert not any("obligation_" in sql for sql, _ in pool.calls)
    admission.run_source_transaction.assert_not_called()
    admission.prepare_dark_submission.assert_not_called()


async def test_off_observation_never_precedes_claim_gates_or_prompt_and_jobs_do_not_import_it(
    monkeypatch,
):
    import builtins

    from butlers.core import scheduler

    _, events, observations = _closed_observer(monkeypatch)
    dispatch = AsyncMock(return_value={"ok": True})
    assert await scheduler.tick(_LocalSchedulePool(_due_prompt(), claim=False), dispatch) == 0
    dispatch.assert_not_called()
    assert not events and not observations

    def failed_prompt(**_):
        raise ValueError("private-failure")

    assert (
        await scheduler.tick(
            _LocalSchedulePool(_due_prompt()),
            dispatch,
            prompt_hooks={"private-task-name": failed_prompt},
        )
        == 0
    )
    assert not events and not observations
    with monkeypatch.context() as patch:
        patch.setattr(scheduler, "_butler_dispatch_gated", AsyncMock(return_value="paused"))
        assert (
            await scheduler.tick(_LocalSchedulePool(_due_prompt()), dispatch, butler_name="health")
            == 0
        )
    with monkeypatch.context() as patch:
        patch.setattr(scheduler, "_defer_cron_for_allowance", AsyncMock(return_value=True))
        assert await scheduler.tick(_LocalSchedulePool(_due_prompt()), dispatch) == 0
    assert not events and not observations
    dispatch.assert_not_called()

    original_import = builtins.__import__
    attempted = []

    def fail_observer_import(name, *args, **kwargs):
        if name == "butlers.core.obligation_admission":
            attempted.append(name)
            raise ImportError("closed observation unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_observer_import)
    job = _due_prompt(dispatch_mode="job", job_name="safety", job_args={"a": 1})
    assert await scheduler.tick(_LocalSchedulePool(job), dispatch) == 1
    dispatch.assert_awaited_once_with(
        job_name="safety", job_args={"a": 1}, trigger_source="schedule:private-task-name"
    )
    assert not attempted and not events
    assert await scheduler.tick(_LocalSchedulePool(_due_prompt()), dispatch) == 1
    assert attempted == ["butlers.core.obligation_admission"]


async def test_off_wake_observes_only_successful_creation_or_exact_local_reconciliation(
    monkeypatch,
):
    from butlers.core import domain_event_wake as wake

    admission, events, observations = _closed_observer(monkeypatch)
    monkeypatch.setattr(admission, "run_source_transaction", AsyncMock(side_effect=AssertionError))
    lifecycle = AsyncMock()
    monkeypatch.setattr(wake, "_open_reaction_lifecycle", lifecycle)
    task_id, event_id = UUID(int=201), UUID(int=202)
    args = dict(
        event_id=event_id,
        event_type="health.snapshot",
        source_butler="health",
        payload={"private": "payload", "valid_until": "expired"},
        subscriber_butler="general",
    )
    create = AsyncMock(return_value=task_id)
    monkeypatch.setattr(wake, "schedule_create", create)
    result = await wake.handle_receive_domain_event(_LocalSchedulePool(lookups=[None]), **args)
    assert result == {
        "status": "ok",
        "state": "task_created",
        "task_id": str(task_id),
        "task_name": wake.task_name_for(event_id, "general"),
    }
    _assert_closed_events(events, ["wake_created"])
    assert observations[0].source_row_id == task_id
    assert observations[0].reported_event_id == event_id
    assert observations[0].owner_domain == "general" and observations[0].nominal_slot is None
    lifecycle.assert_awaited_once()
    prompt = create.await_args.args[3]
    assert "<domain_event>" in prompt and '"valid_until": "expired"' in prompt
    stored = {"id": task_id, "prompt": prompt}
    for lookups in ([stored], [None, stored]):
        create.reset_mock()
        create.side_effect = ValueError("collision")
        replay = await wake.handle_receive_domain_event(
            _LocalSchedulePool(lookups=lookups), **{**args, "payload": {"changed": "untrusted"}}
        )
        assert replay == {**result, "reconciled": True}
    _assert_closed_events(events, ["wake_created", "wake_reconciled", "wake_reconciled"])
    assert all(
        o.authority == "unverified" and o.definition_state == "unavailable" for o in observations
    )
    for lookups, state in (
        ([{"id": task_id, "prompt": "unrelated"}], "conflict"),
        ([None, {"id": task_id, "prompt": "unrelated"}], "conflict"),
        ([None, None], "error"),
    ):
        refused = await wake.handle_receive_domain_event(
            _LocalSchedulePool(lookups=lookups), **args
        )
        assert refused["status"] == state
    assert len(events) == 3 and len(observations) == 3
    lifecycle.assert_awaited_once()
    admission.run_source_transaction.assert_not_called()


async def test_off_builder_invalid_result_trace_or_import_failure_cannot_change_legacy_flow(
    monkeypatch,
):
    import builtins

    from butlers.core import domain_event_wake as wake
    from butlers.core import obligation_admission as admission
    from butlers.core import scheduler

    monkeypatch.setattr(wake, "schedule_create", AsyncMock(return_value=UUID(int=301)))
    monkeypatch.setattr(wake, "_open_reaction_lifecycle", AsyncMock())
    expected = {"sentinel": "unchanged"}
    args = dict(
        event_id=UUID(int=302),
        event_type="event",
        source_butler="health",
        subscriber_butler="general",
        payload={},
    )
    builder = getattr(admission, "build_dark_attempt_observation", None)
    assert builder is not None
    _, events, _ = _closed_observer(monkeypatch)

    def fail(**_):
        raise ValueError("private builder failure")

    for replacement in (fail, lambda **_: object(), lambda **_: {"authority": "verified"}):
        with monkeypatch.context() as patch:
            patch.setattr(admission, "build_dark_attempt_observation", replacement)
            dispatch = AsyncMock(return_value=expected)
            assert await scheduler.tick(_LocalSchedulePool(_due_prompt()), dispatch) == 1
            dispatch.assert_awaited_once()
            assert (
                await wake.handle_receive_domain_event(_LocalSchedulePool(lookups=[None]), **args)
            )["status"] == "ok"
    assert len(events) == 6
    assert all(a["quality"] == "unavailable" and a["authority"] == "unverified" for _, a in events)

    # Trace failures are equally non-authoritative; no error content enters trace.
    def trace_failure(*_, **__):
        raise RuntimeError("private trace failure")

    _, _, _ = _closed_observer(monkeypatch)
    from opentelemetry import trace

    monkeypatch.setattr(trace.get_current_span(), "add_event", trace_failure)
    assert (
        await scheduler.tick(_LocalSchedulePool(_due_prompt()), AsyncMock(return_value=expected))
        == 1
    )
    assert (await wake.handle_receive_domain_event(_LocalSchedulePool(lookups=[None]), **args))[
        "status"
    ] == "ok"
    original_import = builtins.__import__
    attempted = []

    def fail_import(name, *args, **kwargs):
        if name == "butlers.core.obligation_admission":
            attempted.append(name)
            raise ImportError("closed import failure")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_import)
    assert (await wake.handle_receive_domain_event(_LocalSchedulePool(lookups=[None]), **args))[
        "status"
    ] == "ok"
    assert len(attempted) == 1


async def test_off_observer_preserves_cancellation_and_other_base_exceptions(monkeypatch):
    import asyncio

    from butlers.core import domain_event_wake as wake
    from butlers.core import obligation_admission as admission
    from butlers.core import scheduler

    monkeypatch.setattr(wake, "schedule_create", AsyncMock(return_value=UUID(int=401)))
    monkeypatch.setattr(wake, "_open_reaction_lifecycle", AsyncMock())
    for error in (asyncio.CancelledError(), BaseException("closed-test-control")):

        def interrupt(error=error, **_):
            raise error

        monkeypatch.setattr(admission, "build_dark_attempt_observation", interrupt)
        dispatch = AsyncMock()
        with pytest.raises(type(error)):
            await scheduler.tick(_LocalSchedulePool(_due_prompt()), dispatch)
        dispatch.assert_not_called()
        with pytest.raises(type(error)):
            await wake.handle_receive_domain_event(
                _LocalSchedulePool(lookups=[None]),
                event_id=UUID(int=402),
                event_type="event",
                source_butler="health",
                subscriber_butler="general",
                payload={},
            )


def test_off_descriptor_is_immutable_non_authority_with_explicit_unknown_identities():
    from dataclasses import FrozenInstanceError

    from butlers.core.obligation_admission import build_dark_attempt_observation

    observation = build_dark_attempt_observation(
        kind="schedule_attempt",
        phase="post_claim",
        owner_domain="health",
        source_row_id=UUID(int=501),
        nominal_slot=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert observation.mode == "off" and observation.authority == "unverified"
    assert observation.definition_state == "unavailable"
    with pytest.raises(FrozenInstanceError):
        observation.authority = "verified"
    for invalid in (None, "not a UUID or domain", {"private": "payload"}):
        unknown = build_dark_attempt_observation(
            kind="domain_wake",
            phase="wake_created",
            owner_domain=invalid,
            source_row_id=invalid,
            reported_event_id=invalid,
            nominal_slot=datetime(2026, 1, 1),
        )
        assert unknown.owner_domain is None and unknown.source_row_id is None
        assert unknown.reported_event_id is None and unknown.nominal_slot is None
        assert unknown.authority == "unverified"
    with pytest.raises(ValueError):
        build_dark_attempt_observation(kind="verified_goal", phase="post_claim")


def insight_source_fixture():
    """Rollback-aware production SQL protocol; no PG locks/roles/durability claim."""
    import copy
    from datetime import timedelta

    from tests.core.test_obligation_schedule_dispatch import OccurrenceConnection
    from tests.core.test_obligation_schedule_sources import RecordingPool

    class Connection(OccurrenceConnection):
        async def execute(self, sql, *args):
            if sql.lstrip().startswith("DELETE FROM") and "scheduled_tasks" in sql:
                del self.rows[args[0]]
                return "DELETE 1"
            return await super().execute(sql, *args)

        async def fetchrow(self, sql, *args):
            if "current_schema()" in sql:
                return {"schema": "switchboard", "role": self.role, "shadowed": self.shadow}
            return await super().fetchrow(sql, *args)

        async def fetch(self, sql, *args):
            if "FROM public.insight_candidates" in sql:
                assert self.depth == 1 and "FOR UPDATE" in sql
                self.calls.append(sql)
                return copy.deepcopy(self.candidates)
            return await super().fetch(sql, *args)

    now = datetime(2026, 1, 15, 12, tzinfo=UTC)
    rows = [
        {
            "id": UUID(int=n),
            "origin_butler": "health",
            "priority": 95,
            "category": "test",
            "dedup_key": f"health:test:{n}",
            "cooldown_days": None,
            "expires_at": now + timedelta(days=2),
            "message": f"Private note {n}",
            "channel": None,
            "metadata": {"entity_id": "same-cluster"},
            "prepared_action_id": None,
            "created_at": now,
            "premise": None,
        }
        for n in (101, 102)
    ]
    pool = RecordingPool()
    pool.connection = Connection()
    pool.connection.role = "butler_switchboard_rw"
    pool.connection.candidates = copy.deepcopy(rows)
    return pool, rows


@pytest.mark.asyncio
async def test_insight_selected_material_replay_fences_failure_and_off(monkeypatch):
    """Actual helper/repository control species; software, never PG or admission."""
    import copy
    import json

    from butlers.core.obligation_admission import SourceUnavailable
    from butlers.core.obligation_insight_sources import configured_insight_preparer
    from butlers.core.obligation_sources import SourceConflict

    pool, raw = insight_source_fixture()
    monkeypatch.delenv("BUTLERS_INSIGHT_DARK_PREPARATION", raising=False)
    assert configured_insight_preparer(pool, "insight_delivery_cycle") is None
    assert not pool.connection.calls
    monkeypatch.setenv("BUTLERS_INSIGHT_DARK_PREPARATION", "1")
    prepare = configured_insight_preparer(pool, "insight_delivery_cycle")
    # The decorated delivery copy and prompt are the actual model input; raw
    # candidate message remains unchanged. Observations grant no action rights.
    raw[0]["premise"] = {"kind": "observed"}
    pool.connection.candidates = copy.deepcopy(raw)
    selected = copy.deepcopy(raw)
    selected[0]["message"] += " (as of 2026-01-15 12:00 UTC)"
    from butlers.tools.switchboard.insight.broker import _cluster_synthesis_prompt

    context = {
        "clusters": [
            {"ids": [str(c["id"]) for c in selected], "prompt": _cluster_synthesis_prompt(selected)}
        ],
        "settings": {"verbosity": "normal"},
        "effective_budget": 2,
        "selection_reference": datetime(2026, 1, 15, 12, tzinfo=UTC),
        "urgent_only": False,
        "daily_hold_mode": True,
        "category_budget_reasons": {},
        "premise_verdicts": {},
        "prepared_action_statuses": {},
    }
    assert await prepare(raw, selected, context) == "staged_unverified"
    conn = pool.connection
    original = copy.deepcopy((conn.definitions, conn.revisions, conn.occurrences))
    assert await prepare(raw, selected, context) == "staged_unverified"
    assert (conn.definitions, conn.revisions, conn.occurrences) == original
    value = next(iter(conn.revisions.values()))
    assert value["payload"]["raw"][0]["message"] == "Private note 101"
    assert value["payload"]["selected"][0]["message"] == selected[0]["message"]
    assert value["payload"]["context"]["settings"] == context["settings"]
    assert value["payload"]["admission"] == "unverified_unavailable"
    for fault in ("edited", "deleted", "reordered", "context", "decorated", "wrongrole", "shadow"):
        conn.candidates = copy.deepcopy(raw)
        given_raw, given_selected, given_context = copy.deepcopy((raw, selected, context))
        if fault == "edited":
            conn.candidates[0]["message"] = "concurrent different content"
        elif fault == "deleted":
            conn.candidates.pop()
        elif fault == "reordered":
            given_raw.reverse()
            given_selected.reverse()
        elif fault == "context":
            given_context["settings"]["verbosity"] = "verbose"
        elif fault == "decorated":
            given_selected[0]["message"] = "contradictory replay"
        elif fault == "wrongrole":
            conn.role = "butler_health_rw"
        else:
            conn.shadow = True
        with pytest.raises(SourceConflict):
            await prepare(given_raw, given_selected, given_context)
        assert (conn.definitions, conn.revisions, conn.occurrences) == original
        conn.role, conn.shadow = "butler_switchboard_rw", False
    fresh, candidates = insight_source_fixture()
    fresh.connection.fail_outbox = True
    fresh.connection.candidates = copy.deepcopy(raw)
    with pytest.raises(SourceUnavailable):
        await configured_insight_preparer(fresh, "insight_delivery_cycle")(raw, selected, context)
    assert not fresh.connection.definitions and not fresh.connection.occurrences
    assert not fresh.connection.revisions and not fresh.connection.outbox
    # Existing body roundtrip remains exact when no optional source payload exists.
    from butlers.core.obligation_sources import DefinitionRevision

    assert DefinitionRevision.decode(value).encode() == value
    bad = copy.deepcopy(value)
    bad["payload"]["selected"][0]["message"] = "tampered stored content"
    with pytest.raises(DeclarationError, match="invalid_source_payload"):
        DefinitionRevision.decode(bad)
    assert json.loads(DefinitionRevision.decode(value).payload_json) == value["payload"]

    # A commit/release reply loss is UNKNOWN while its exact original durable
    # source is reconciled; a fresh identity must not mask the committed write.
    from contextlib import asynccontextmanager

    lost, _ = insight_source_fixture()
    lost.connection.candidates = copy.deepcopy(raw)
    acquire = lost.acquire
    fail_release = True

    @asynccontextmanager
    async def lost_reply():
        nonlocal fail_release
        async with acquire() as connection:
            yield connection
        if fail_release:
            fail_release = False
            raise OSError("controlled committed reply loss")

    lost.acquire = lost_reply
    pending = configured_insight_preparer(lost, "insight_delivery_cycle")
    with pytest.raises(SourceUnavailable):
        await pending(raw, selected, context)
    assert len(lost.connection.definitions) == len(lost.connection.occurrences) == 1
    assert await pending(raw, selected, context) == "staged_unverified"
    assert len(lost.connection.definitions) == len(lost.connection.occurrences) == 1

    import asyncio

    cancelled, _ = insight_source_fixture()
    entered, release = asyncio.Event(), asyncio.Event()
    original_fetch = cancelled.connection.fetch

    async def paused(sql, *args):
        if "FROM public.insight_candidates" in sql:
            entered.set()
            await release.wait()
        return await original_fetch(sql, *args)

    cancelled.connection.fetch = paused
    operation = asyncio.create_task(
        configured_insight_preparer(cancelled, "insight_delivery_cycle")(raw, selected, context)
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(operation, 1)
        assert cancelled.connection.depth == 0
        assert not cancelled.connection.definitions and not cancelled.connection.outbox
    finally:
        release.set()
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)


async def test_domain_preparation_repeated_cancel_retains_absolute_deadline_and_cleanup(
    tmp_path, monkeypatch
):
    """Real registered consumer, retained source connection; no PG proof."""
    import asyncio

    from butlers.core import obligation_domain_sources as domain
    from tests.core.test_domain_event_wake import domain_source_consumer

    daemon, pool, receive = await domain_source_consumer(tmp_path, monkeypatch)
    runtime, c = daemon.obligation_domain_runtime, pool.connection
    entered, release = asyncio.Event(), asyncio.Event()
    fetchrow = c.fetchrow

    async def resistant(sql, *args):
        if "FROM public.domain_events" in sql:
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass
        return await fetchrow(sql, *args)

    c.fetchrow = resistant
    monkeypatch.setattr(domain, "PREPARATION_SECONDS", 0.08)
    args = {
        "event_id": str(c.event["id"]),
        "event_type": c.event["event_type"],
        "source_butler": "travel",
        "payload": c.event["payload"],
    }
    operation = asyncio.create_task(receive(**args))
    first_task = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        first_task, first_deadline = next(iter(runtime._pending.items()))
        operation.cancel()
        await asyncio.sleep(0)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert not first_task.done() and runtime._pending[first_task] == first_deadline
        assert len(c.rows) == 1 and c.depth == 1
        with pytest.raises(RuntimeError, match="domain_preparation_cleanup_incomplete"):
            await runtime.aclose()
        assert runtime._pending[first_task] == first_deadline
        assert await runtime.prepare(next(iter(c.rows)), **args) is None
        release.set()
        await asyncio.wait_for(asyncio.shield(first_task), 1)
        await runtime.aclose()
        assert c.depth == 0 and not runtime._pending
        # Completion after caller cancellation was never reported as a successful
        # committed callback. Explicit readback may now reconcile the stable key.
        assert len(c.occurrences) == 1
    finally:
        release.set()
        if not operation.done():
            operation.cancel()
        await asyncio.wait_for(asyncio.gather(operation, return_exceptions=True), 1)
        if first_task is not None:
            await asyncio.wait_for(asyncio.gather(first_task, return_exceptions=True), 1)
        await runtime.aclose()


@pytest.mark.parametrize("outcome", ["healthy", "refused", "cancelled", "downstream_failure"])
async def test_domain_runtime_close_failure_still_attempts_daemon_downstream_cleanup(
    tmp_path, monkeypatch, outcome
):
    import asyncio
    from types import SimpleNamespace

    from butlers import lifecycle
    from tests.core.test_domain_event_wake import domain_source_consumer

    daemon, _, _ = await domain_source_consumer(tmp_path, monkeypatch)
    runtime = daemon.obligation_domain_runtime
    attempted = []

    async def close():
        if outcome == "refused":
            raise RuntimeError("domain_preparation_cleanup_incomplete")
        if outcome == "cancelled":
            raise asyncio.CancelledError
        await runtime.aclose()

    original_close = runtime.aclose

    async def finish_close():
        if outcome in {"refused", "cancelled"}:
            return await close()
        await original_close()

    async def release(name):
        attempted.append(name)
        if name == "module" and outcome == "downstream_failure":
            raise OSError("synthetic downstream failure")

    monkeypatch.setattr(runtime, "aclose", finish_close)
    daemon.spawner = None
    daemon.config.shutdown_timeout_s = 1
    module = SimpleNamespace(name="fixture", on_shutdown=lambda: release("module"))
    daemon._modules = [module]
    daemon.blob_store = SimpleNamespace(close=lambda: release("blob"))
    daemon._audit_db = SimpleNamespace(close=lambda: release("audit"))
    daemon._shared_credentials_db = SimpleNamespace(close=lambda: release("credentials"))
    daemon.db.close = lambda: release("main")
    expected = {
        "refused": RuntimeError,
        "cancelled": asyncio.CancelledError,
        "downstream_failure": BaseExceptionGroup,
    }.get(outcome)
    if expected:
        with pytest.raises(expected):
            await lifecycle._shutdown_resources(daemon)
    else:
        await lifecycle._shutdown_resources(daemon)
    assert attempted == ["module", "blob", "audit", "credentials", "main"]
    assert not getattr(daemon, "_admission_cleanup_tasks", set())


@pytest.mark.asyncio
async def test_insight_private_reference_retains_complete_body_and_legacy_status(monkeypatch):
    """Committed reference is immutable material, never evidence or a runner payload."""
    import copy
    import json

    from butlers.core.obligation_admission import DarkSubmission
    from butlers.core.obligation_insight_sources import configured_insight_preparer

    pool, rows = insight_source_fixture()
    raw = [rows[0]]
    pool.connection.candidates = copy.deepcopy(raw)
    context = dict(
        urgent_only=False,
        daily_hold_mode=True,
        selection_reference=raw[0]["created_at"],
        settings={"verbosity": "normal"},
        effective_budget=1,
        category_budget_reasons={},
        premise_verdicts={},
        prepared_action_statuses={},
        clusters=[{"ids": [str(raw[0]["id"])], "prompt": None}],
    )
    monkeypatch.setenv("BUTLERS_INSIGHT_DARK_PREPARATION", "1")
    prepare = configured_insight_preparer(pool, "insight_delivery_cycle")
    reference = await prepare.prepare_committed(raw, raw, context)
    assert type(reference) is DarkSubmission and pool.connection.depth == 0
    payload = json.loads(reference.definition.payload_json)
    assert payload["raw"][0]["message"] == raw[0]["message"]
    assert payload["context"]["settings"] == context["settings"]
    assert reference.definition.creator is None and reference.obligation.created_exposed
    context["settings"]["verbosity"] = "off"
    assert (
        json.loads(reference.definition.payload_json)["context"]["settings"]["verbosity"]
        == "normal"
    )
    context["settings"]["verbosity"] = "normal"
    assert await prepare(raw, raw, context) == "staged_unverified"
    assert len(pool.connection.occurrences) == 1
