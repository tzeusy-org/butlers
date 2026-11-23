"""REQ-condition-evaluation-001/002: the S4 evaluator through its production API."""

from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

import pytest

from butlers.conditions import (
    ClockReading,
    ConditionEvaluator,
    ConditionEvent,
    ConditionsUnavailable,
    Grant,
    RegistrationRefused,
    Snapshot,
    State,
    Tri,
    resolve_clock,
)
from butlers.conditions.memory import InMemoryConditionStore
from butlers.conditions.predicate import PredicateError, validate
from butlers.core.goal_labels import CATEGORIES, Label

#: A non-trivial registered label snapshot, so a default or dropped label cannot pass.
LABEL = Label(frozenset({"owner"}), frozenset({"owner", "domain:calendar"}), frozenset({"routine"}))
NS = UUID("11111111-1111-1111-1111-111111111111")
GOAL = str(uuid5(NS, "goal"))
SUBJECT = str(uuid5(NS, "subject"))
EFFECT = str(uuid5(NS, "effect"))
BINDING = str(uuid5(NS, "binding"))
PLACE = str(uuid5(NS, "place"))
EXPECTED = str(uuid5(NS, "expectation-subject"))
ROUTINE = str(uuid5(NS, "routine"))
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
EXPIRES = NOW + timedelta(hours=2)
SOURCES = frozenset(
    {
        "goal.decision",
        "goal.inbox",
        "goal.effect",
        "clock",
        "perception:owner.place",
        "perception:expect.window",
        "perception:owner.mode",
        "condition:finance:bill-overdue",
        "read_check:finance.balance",
        "bundle:delay-commitment",
    }
)
GRANT = Grant(
    goal_id=GOAL,
    sources=SOURCES,
    decisions=frozenset({SUBJECT}),
    effects=frozenset({EFFECT}),
    bindings=frozenset({BINDING}),
    subjects=frozenset({PLACE, EXPECTED, ROUTINE}),
)

DECISION = {"tag": "decision", "subject_id": SUBJECT, "outcome": "approved"}
INPUT = {"tag": "owner_input", "goal_id": GOAL, "binding_ref": BINDING}
EFFECT_LEAF = {"tag": "effect_settled", "effect_id": EFFECT, "status_set": ["succeeded"]}
AT = {"tag": "at", "instant": (NOW + timedelta(minutes=30)).isoformat(), "zone": "UTC"}
TRANSITION = {
    "tag": "transition",
    "kind": "owner.place",
    "subject": PLACE,
    "from": "known",
    "to": "unknown",
}
EXPECTATION = {
    "tag": "expectation",
    "expectation_kind": "expect.window",
    "subject": EXPECTED,
    "deviation": "missed",
}
ROUTINE_DEVIATION = {"tag": "routine_deviation", "routine_id": ROUTINE}
CONDITION = {"tag": "condition", "source": "finance:bill-overdue", "state": "opened"}
MODE = {"tag": "mode", "mode": "asleep", "change": "entered"}
READ_CHECK = {
    "tag": "read_check",
    "capability": "finance.balance",
    "field": "available",
    "op": "lt",
    "value": 100,
}
BUNDLE = {"tag": "bundle", "bundle_id": "delay-commitment"}


def event(tag, source, position, data, *, at=NOW, **flags):
    eid = str(uuid5(NS, f"{tag}:{position}:{sorted(data.items())}"))
    return ConditionEvent(
        id=eid, source=source, position=position, at=at, tag=tag, data=data, **flags
    )


def decision_ev(position=1, outcome="approved", **kw):
    return event(
        "decision", "goal.decision", position, {"subject_id": SUBJECT, "outcome": outcome}, **kw
    )


def input_ev(position=1, **kw):
    return event(
        "owner_input", "goal.inbox", position, {"goal_id": GOAL, "binding_ref": BINDING}, **kw
    )


def effect_ev(position=1, status="succeeded", **kw):
    return event(
        "effect_settled", "goal.effect", position, {"effect_id": EFFECT, "status": status}, **kw
    )


def transition_ev(position=1, to="unknown", **kw):
    data = {"kind": "owner.place", "subject": PLACE, "from": "known", "to": to}
    return event("transition", "perception:owner.place", position, data, **kw)


async def register(evaluator, predicate, *, snapshot=None, instance_id="c1", **kw):
    return await evaluator.register(
        instance_id=instance_id,
        predicate=predicate,
        grant=GRANT,
        goal_id=GOAL,
        snapshot=snapshot or Snapshot(now=NOW),
        expires_at=EXPIRES,
        **{"label": LABEL, **kw},
    )


@pytest.fixture
def store():
    return InMemoryConditionStore()


@pytest.fixture
def evaluator(store):
    return ConditionEvaluator(store)


def expectation_ev(position=1, deviation="missed", **kw):
    data = {"expectation_kind": "expect.window", "subject": EXPECTED, "deviation": deviation}
    return event("expectation", "perception:expect.window", position, data, **kw)


def routine_ev(position=1, deviation="missed", **kw):
    data = {"routine_id": ROUTINE, "deviation": deviation}
    return event("routine_deviation", "perception:expect.window", position, data, **kw)


def condition_ev(position=1, state="opened", **kw):
    data = {"source": "finance:bill-overdue", "state": state}
    return event("condition", "condition:finance:bill-overdue", position, data, **kw)


def mode_ev(position=1, change="entered", **kw):
    return event(
        "mode", "perception:owner.mode", position, {"mode": "asleep", "change": change}, **kw
    )


def read_ev(position: int = 1, value: object = 50, **kw):
    data = {"capability": "finance.balance", "field": "available", "value": value}
    kw.setdefault("trusted", True)
    return event("read_check", "read_check:finance.balance", position, data, **kw)


def bundle_ev(position=1, **kw):
    return event(
        "bundle", "bundle:delay-commitment", position, {"bundle_id": "delay-commitment"}, **kw
    )


M7_OCCURRENCES = {
    "expectation": (EXPECTATION, expectation_ev, "perception:expect.window"),
    "routine_deviation": (ROUTINE_DEVIATION, routine_ev, "perception:expect.window"),
    "condition": (CONDITION, condition_ev, "condition:finance:bill-overdue"),
    "mode": (MODE, mode_ev, "perception:owner.mode"),
    "read_check": (READ_CHECK, read_ev, "read_check:finance.balance"),
    "bundle": (BUNDLE, bundle_ev, "bundle:delay-commitment"),
}

# (leaf, firing event factory, non-firing event factory, source name)
LEAVES = {
    "decision": (
        DECISION,
        lambda: decision_ev(),
        lambda: decision_ev(outcome="rejected"),
        "goal.decision",
    ),
    "owner_input": (INPUT, lambda: input_ev(), lambda: input_ev(measurable=False), "goal.inbox"),
    "effect_settled": (
        EFFECT_LEAF,
        lambda: effect_ev(),
        lambda: effect_ev(status="failed"),
        "goal.effect",
    ),
    "transition": (
        TRANSITION,
        lambda: transition_ev(),
        lambda: transition_ev(to="known"),
        "perception:owner.place",
    ),
    "expectation": (
        EXPECTATION,
        lambda: expectation_ev(),
        lambda: expectation_ev(deviation="within"),
        "perception:expect.window",
    ),
    "routine_deviation": (
        ROUTINE_DEVIATION,
        lambda: routine_ev(),
        lambda: routine_ev(deviation="within"),
        "perception:expect.window",
    ),
    "condition": (
        CONDITION,
        lambda: condition_ev(),
        lambda: condition_ev(state="resolved"),
        "condition:finance:bill-overdue",
    ),
    "mode": (MODE, lambda: mode_ev(), lambda: mode_ev(change="exited"), "perception:owner.mode"),
    "read_check": (
        READ_CHECK,
        lambda: read_ev(),
        lambda: read_ev(value=500),
        "read_check:finance.balance",
    ),
    "bundle": (
        BUNDLE,
        lambda: bundle_ev(),
        lambda: bundle_ev(measurable=False),
        "bundle:delay-commitment",
    ),
}


@pytest.mark.parametrize("name", LEAVES)
class TestLeafMatrix:
    async def test_fires_on_matching_event(self, evaluator, name):
        leaf, fire, _, _ = LEAVES[name]
        await register(evaluator, leaf)
        out = await evaluator.evaluate("c1", [fire()], now=NOW)
        assert out.instance.state is State.FIRED and out.intent is not None
        assert str(out.intent.event_id) == fire().id

    async def test_no_fire_on_non_matching_event(self, evaluator, name):
        leaf, _, miss, _ = LEAVES[name]
        await register(evaluator, leaf)
        out = await evaluator.evaluate("c1", [miss()], now=NOW)
        assert out.instance.state is State.OPEN and out.intent is None

    async def test_duplicate_event_does_not_refire(self, evaluator, store, name):
        leaf, fire, _, _ = LEAVES[name]
        await register(evaluator, leaf)
        first = await evaluator.evaluate("c1", [fire()], now=NOW)
        again = await evaluator.evaluate("c1", [fire(), fire()], now=NOW)
        assert again.intent == first.intent
        assert len(store.intents) == 1

    async def test_unmeasurable_source_is_unknown_not_false(self, evaluator, name):
        leaf, _, _, source = LEAVES[name]
        snap = Snapshot(now=NOW, unmeasurable=frozenset({source}))
        out = await register(evaluator, leaf, snapshot=snap)
        assert out.instance.result is Tri.UNKNOWN and out.intent is None

    async def test_already_satisfied_at_registration_fires_once(self, evaluator, store, name):
        leaf, _, _, _ = LEAVES[name]
        snap = Snapshot(
            now=NOW,
            decisions={SUBJECT: "approved"},
            inputs=frozenset({BINDING}),
            effects={EFFECT: "succeeded"},
        )
        if name == "transition" or name in M7_OCCURRENCES:
            pytest.skip("an occurrence is an edge; a snapshot cannot witness it")
        out = await register(evaluator, leaf, snapshot=snap)
        assert out.instance.state is State.FIRED and out.intent.reason == "fired:registration"
        retry = await register(evaluator, leaf, snapshot=snap)
        assert retry.intent == out.intent and len(store.intents) == 1


class TestAt:
    async def test_fires_when_clock_reaches_instant(self, evaluator):
        await register(evaluator, AT)
        early = await evaluator.evaluate("c1", [], now=NOW + timedelta(minutes=29))
        assert early.instance.state is State.OPEN
        on_time = await evaluator.evaluate("c1", [], now=NOW + timedelta(minutes=30))
        assert on_time.intent.reason == "fired:clock"

    async def test_clock_jump_fires_once_late_before_expiry(self, evaluator, store):
        await register(evaluator, AT)
        out = await evaluator.evaluate("c1", [], now=NOW + timedelta(hours=1))
        assert out.intent.reason == "fired:clock_late"
        for minutes in (61, 62, 5):  # later ticks and a backwards jump
            await evaluator.evaluate("c1", [], now=NOW + timedelta(minutes=minutes))
        assert len(store.intents) == 1

    async def test_jump_past_expiry_expires_instead_of_firing(self, evaluator, store):
        await register(evaluator, AT)
        out = await evaluator.evaluate("c1", [], now=EXPIRES + timedelta(minutes=1))
        assert out.instance.state is State.EXPIRED and not store.intents

    async def test_unavailable_clock_is_unknown(self, evaluator):
        await register(evaluator, AT)
        out = await evaluator.evaluate(
            "c1", [], now=NOW + timedelta(hours=1), unmeasurable=frozenset({"clock"})
        )
        assert out.instance.result is Tri.UNKNOWN and out.intent is None


class TestTransitionHorizon:
    async def test_event_older_than_ten_minutes_never_fires(self, evaluator):
        await register(evaluator, TRANSITION)
        stale = transition_ev(at=NOW - timedelta(minutes=11))
        out = await evaluator.evaluate("c1", [stale], now=NOW)
        assert out.instance.state is State.OPEN

    async def test_exactly_ten_minutes_still_counts(self, evaluator):
        await register(evaluator, TRANSITION)
        edge = transition_ev(at=NOW - timedelta(minutes=10))
        assert (await evaluator.evaluate("c1", [edge], now=NOW)).intent is not None

    async def test_future_dated_event_is_ignored_and_does_not_move_the_cursor(self, evaluator):
        await register(evaluator, TRANSITION)
        future = transition_ev(at=NOW + timedelta(hours=1))
        out = await evaluator.evaluate("c1", [future], now=NOW)
        assert out.instance.state is State.OPEN and out.intent is None
        assert out.instance.cursors == {}

    async def test_future_dated_event_counts_once_the_clock_reaches_it(self, evaluator):
        await register(evaluator, TRANSITION)
        future = transition_ev(at=NOW + timedelta(minutes=5))
        assert (await evaluator.evaluate("c1", [future], now=NOW)).intent is None
        later = await evaluator.evaluate("c1", [future], now=NOW + timedelta(minutes=5))
        assert later.intent is not None

    async def test_small_clock_skew_is_tolerated(self, evaluator):
        await register(evaluator, TRANSITION)
        skewed = transition_ev(at=NOW + timedelta(seconds=30))
        assert (await evaluator.evaluate("c1", [skewed], now=NOW)).intent is not None

    @pytest.mark.parametrize(
        "make",
        [
            lambda: transition_ev(late=True),
            lambda: transition_ev(retracted=True),
            lambda: transition_ev(record=True),
        ],
        ids=["late", "retracted", "record"],
    )
    async def test_late_or_retracted_never_fires(self, evaluator, make):
        await register(evaluator, TRANSITION)
        out = await evaluator.evaluate("c1", [make()], now=NOW)
        assert out.instance.state is State.OPEN


class TestComposition:
    async def test_all_waits_for_every_leaf(self, evaluator):
        await register(evaluator, {"tag": "all", "children": [DECISION, INPUT]})
        half = await evaluator.evaluate("c1", [decision_ev()], now=NOW)
        assert half.instance.state is State.OPEN and half.instance.result is Tri.FALSE
        done = await evaluator.evaluate("c1", [input_ev(2)], now=NOW)
        assert done.instance.state is State.FIRED

    async def test_any_fires_on_first(self, evaluator):
        await register(evaluator, {"tag": "any", "children": [DECISION, INPUT]})
        assert (await evaluator.evaluate("c1", [input_ev()], now=NOW)).intent is not None

    async def test_unknown_stays_explicit_through_all_any(self, evaluator):
        unknown = Snapshot(now=NOW, unmeasurable=frozenset({"goal.inbox"}))
        a = await register(
            evaluator,
            {"tag": "all", "children": [DECISION, INPUT]},
            snapshot=unknown,
            instance_id="a",
        )
        assert a.instance.result is Tri.FALSE  # known false is sufficient for all
        b = await register(
            evaluator,
            {"tag": "any", "children": [DECISION, INPUT]},
            snapshot=unknown,
            instance_id="b",
        )
        assert b.instance.result is Tri.UNKNOWN  # false + unknown is not false
        c = await register(
            evaluator,
            {"tag": "any", "children": [DECISION, INPUT]},
            snapshot=Snapshot(
                now=NOW, decisions={SUBJECT: "approved"}, unmeasurable=frozenset({"goal.inbox"})
            ),
            instance_id="c",
        )
        assert c.instance.state is State.FIRED  # known true is sufficient for any

    async def test_out_of_order_batch_fires_once(self, evaluator, store):
        await register(evaluator, {"tag": "all", "children": [DECISION, EFFECT_LEAF]})
        batch = [effect_ev(5), decision_ev(2), decision_ev(1, outcome="rejected")]
        out = await evaluator.evaluate("c1", batch, now=NOW)
        assert out.instance.state is State.FIRED
        await evaluator.evaluate("c1", list(reversed(batch)), now=NOW)
        assert len(store.intents) == 1


class TestRefusal:
    @pytest.mark.parametrize(
        "bad",
        [
            "decision approved",  # free text
            {"tag": "sql", "query": "select 1"},
            {**DECISION, "extra": 1},
            {"tag": "decision", "subject_id": SUBJECT},
            {**DECISION, "outcome": "maybe"},
            {**AT, "zone": "Mars/Base"},
            {**AT, "instant": "2026-11-09T12:00:00+08:00"},
            {**TRANSITION, "kind": "owner.vibes"},
            {"tag": "all", "children": []},
            {
                "tag": "all",
                "children": [{"tag": "all", "children": [{"tag": "all", "children": [DECISION]}]}],
            },
            {"tag": "any", "children": [DECISION] * 17},
            {"tag": "effect_settled", "effect_id": EFFECT, "status_set": []},
            {"tag": "decision", "subject_id": "not-a-uuid", "outcome": "approved"},
            {**EXPECTATION, "deviation": "late"},
            {**EXPECTATION, "subject": "not-a-uuid"},
            {**EXPECTATION, "expectation_kind": "Has Spaces"},
            {**EXPECTATION, "expectation_kind": "routine.window"},  # well-formed, not a kind
            {**EXPECTATION, "expectation_kind": "expect.unregistered"},
            {**ROUTINE_DEVIATION, "routine_id": "not-a-uuid"},
            {**CONDITION, "state": "open"},
            {**CONDITION, "source": "x" * 129},
            {**MODE, "mode": "party"},
            {**MODE, "change": "toggled"},
            {**READ_CHECK, "op": "like"},
            {**READ_CHECK, "value": None},
            {**READ_CHECK, "value": [1]},
            {**READ_CHECK, "value": True},  # ordered comparison against a boolean
            {**READ_CHECK, "value": float("inf")},
            {**READ_CHECK, "op": "eq", "value": {"k": 1}},
            {**READ_CHECK, "capability": "select 1; --"},
            {**READ_CHECK, "field": "A" * 65},
            {**READ_CHECK, "template": "free text"},
            {**BUNDLE, "bundle_id": ""},
            {"tag": "bundle"},
        ],
    )
    async def test_invalid_predicate_refuses_without_side_effect(self, evaluator, store, bad):
        with pytest.raises(RegistrationRefused):
            await register(evaluator, bad)
        assert not store.instances and not store.receipts

    def test_depth_two_is_allowed_and_leaf_cap_is_sixteen(self):
        nested = {"tag": "all", "children": [{"tag": "any", "children": [DECISION, INPUT]}]}
        assert validate(nested) == nested
        wide = {"tag": "all", "children": [{"tag": "any", "children": [DECISION] * 9}] * 2}
        with pytest.raises(PredicateError):
            validate(wide)

    def test_size_cap(self):
        big = {
            "tag": "effect_settled",
            "effect_id": EFFECT,
            "status_set": ["succeeded"],
            "pad": "x" * 9000,
        }
        with pytest.raises(PredicateError):
            validate(big)

    @pytest.mark.parametrize(
        "leaf",
        [
            {**DECISION, "subject_id": str(uuid5(NS, "other"))},
            {**INPUT, "goal_id": str(uuid5(NS, "other-goal"))},
            {**INPUT, "binding_ref": str(uuid5(NS, "other"))},
            {**EFFECT_LEAF, "effect_id": str(uuid5(NS, "other"))},
            {**TRANSITION, "subject": str(uuid5(NS, "other"))},
            {**TRANSITION, "subject": None},
        ],
    )
    async def test_identity_outside_grant_refuses(self, evaluator, store, leaf):
        with pytest.raises(RegistrationRefused):
            await register(evaluator, leaf)
        assert not store.instances

    async def test_undeclared_source_refuses(self, evaluator, store):
        grant = Grant(
            goal_id=GOAL, sources=frozenset({"goal.inbox"}), decisions=frozenset({SUBJECT})
        )
        with pytest.raises(RegistrationRefused):
            await evaluator.register(
                instance_id="c1",
                predicate=DECISION,
                grant=grant,
                goal_id=GOAL,
                label=LABEL,
                snapshot=Snapshot(now=NOW),
                expires_at=EXPIRES,
            )

    async def test_goal_mismatch_and_unbounded_expiry_refuse(self, evaluator):
        with pytest.raises(RegistrationRefused):
            await evaluator.register(
                instance_id="c1",
                predicate=DECISION,
                grant=GRANT,
                goal_id=str(uuid5(NS, "x")),
                label=LABEL,
                snapshot=Snapshot(now=NOW),
                expires_at=EXPIRES,
            )
        with pytest.raises(RegistrationRefused):
            await evaluator.register(
                instance_id="c1",
                predicate=DECISION,
                grant=GRANT,
                goal_id=GOAL,
                label=LABEL,
                snapshot=Snapshot(now=NOW),
                expires_at=NOW + timedelta(days=400),
            )
        with pytest.raises(RegistrationRefused):
            await evaluator.register(
                instance_id="c1",
                predicate=DECISION,
                grant=GRANT,
                goal_id=GOAL,
                label=LABEL,
                snapshot=Snapshot(now=NOW),
                expires_at=NOW,
            )

    async def test_reregistering_changed_predicate_refuses(self, evaluator):
        await register(evaluator, DECISION)
        with pytest.raises(RegistrationRefused):
            await register(evaluator, INPUT)

    async def test_registration_does_not_mutate_caller_predicate(self, evaluator):
        pred = copy.deepcopy(DECISION)
        await register(evaluator, pred)
        assert pred == DECISION


class TestExactlyOneFire:
    async def test_concurrent_evaluators_yield_one_intent(self, store):
        evaluators = [ConditionEvaluator(store) for _ in range(8)]
        await register(evaluators[0], DECISION)
        outs = await asyncio.gather(
            *(e.evaluate("c1", [decision_ev()], now=NOW) for e in evaluators)
        )
        assert len(store.intents) == 1
        assert {o.intent and o.intent.fire_key for o in outs} == {next(iter(store.intents))}
        assert sum(1 for r in store.receipts if r.reason == "fired:event") == 1

    async def test_crash_after_commit_replays_same_intent(self, store):
        crashing = ConditionEvaluator(store)
        await register(crashing, DECISION)
        outcome = await crashing.evaluate("c1", [decision_ev()], now=NOW)
        # The caller crashes before acknowledging; a fresh evaluator replays from the cursor.
        restarted = ConditionEvaluator(store)
        replay = await restarted.evaluate("c1", [decision_ev()], now=NOW)
        assert replay.intent == outcome.intent and len(store.intents) == 1
        for _ in range(2):
            claim = await restarted.consume_fire("c1", "runner-a")
            assert claim is not None and claim.claimed_by == "runner-a"
        assert await restarted.consume_fire("c1", "runner-b") is None

    async def test_rollback_loses_nothing(self, store):
        ev = ConditionEvaluator(store)
        await register(ev, DECISION)
        before = (dict(store.instances), list(store.receipts), dict(store.intents))
        original = store.transaction

        def failing():
            ctx = original()

            class Ctx:
                async def __aenter__(self):
                    tx = await ctx.__aenter__()
                    real = tx.append_receipt

                    async def boom(receipt):
                        await real(receipt)
                        raise ConnectionError("commit lost")

                    tx.append_receipt = boom
                    return tx

                async def __aexit__(self, *exc):
                    return await ctx.__aexit__(*exc)

            return Ctx()

        store.transaction = failing
        with pytest.raises(ConditionsUnavailable):
            await ev.evaluate("c1", [decision_ev()], now=NOW)
        store.transaction = original
        assert (dict(store.instances), list(store.receipts), dict(store.intents)) == before
        out = await ev.evaluate("c1", [decision_ev()], now=NOW)
        assert out.intent is not None  # accepted work retained; fires on retry

    async def test_closed_instances_ignore_late_replay(self, evaluator, store):
        await register(evaluator, DECISION, instance_id="expired")
        await evaluator.evaluate("expired", [], now=EXPIRES)
        await register(evaluator, DECISION, instance_id="stopped")
        await evaluator.cancel("stopped", now=NOW)
        for iid in ("expired", "stopped"):
            out = await evaluator.evaluate(iid, [decision_ev()], now=NOW)
            assert out.intent is None
        assert not store.intents

    async def test_post_expiry_event_cannot_fire(self, evaluator):
        await register(evaluator, DECISION)
        late = decision_ev(at=EXPIRES + timedelta(seconds=1))
        out = await evaluator.evaluate("c1", [late], now=NOW)
        assert out.instance.state is State.OPEN

    async def test_cursor_advances_per_source(self, evaluator):
        await register(evaluator, {"tag": "all", "children": [DECISION, INPUT]})
        out = await evaluator.evaluate("c1", [decision_ev(7)], now=NOW)
        assert dict(out.instance.cursors) == {"goal.decision": 7}

    async def test_fired_instance_is_never_reset_by_reregistration(self, evaluator, store):
        await register(evaluator, DECISION)
        await evaluator.evaluate("c1", [decision_ev()], now=NOW)
        again = await register(evaluator, DECISION)
        assert again.instance.state is State.FIRED and len(store.intents) == 1


class TestAvailability:
    async def test_store_outage_reports_dependency_and_retains_work(self):
        calls = []

        class Reporter:
            async def report(self, dependency, state, reason):
                calls.append((dependency, state))

        class Down:
            def transaction(self):
                raise ConnectionError("db down")

        ev = ConditionEvaluator(Down(), reporter=Reporter())
        with pytest.raises(ConditionsUnavailable):
            await ev.evaluate("c1", [decision_ev()], now=NOW)
        assert calls == [("system.dep.conditions", "down")]


class TestClock:
    def test_owner_then_home_then_utc_without_hidden_zone(self):
        owner = ClockReading("Europe/Paris", "owner", "phone", NOW)
        assert resolve_clock(NOW, owner=owner, home_zone="Asia/Tokyo") is owner
        assert resolve_clock(NOW, home_zone="Asia/Tokyo").zone == "Asia/Tokyo"
        fallback = resolve_clock(NOW)
        assert (fallback.zone, fallback.basis) == ("UTC", "utc_fallback")
        assert resolve_clock(NOW, home_zone="Not/AZone").zone == "UTC"

    def test_non_utc_instant_refused(self):
        with pytest.raises(ValueError):
            resolve_clock(datetime(2026, 1, 1))


class TestCursorBound:
    async def test_rearm_with_prior_cursor_does_not_refire_on_old_event(self, evaluator):
        await register(evaluator, TRANSITION, instance_id="g1")
        first = await evaluator.evaluate("g1", [transition_ev(position=5)], now=NOW)
        g2 = await register(
            evaluator,
            TRANSITION,
            instance_id="g2",
            generation=2,
            cursors=dict(first.instance.cursors),
        )
        replay = await evaluator.evaluate("g2", [transition_ev(position=5)], now=NOW)
        assert g2.instance.state is State.OPEN and replay.instance.state is State.OPEN
        fresh = await evaluator.evaluate("g2", [transition_ev(position=6)], now=NOW)
        assert fresh.instance.state is State.FIRED

    async def test_event_below_registration_cursor_is_ignored(self, evaluator):
        await register(evaluator, DECISION, cursors={"goal.decision": 10})
        out = await evaluator.evaluate("c1", [decision_ev(position=3)], now=NOW)
        assert out.instance.state is State.OPEN
        edge = await evaluator.evaluate("c1", [decision_ev(position=11)], now=NOW)
        assert edge.instance.state is State.FIRED


class TestExpiryWins:
    async def test_event_delivered_after_expiry_never_fires(self, evaluator, store):
        await register(evaluator, DECISION)
        out = await evaluator.evaluate(
            "c1", [decision_ev(at=NOW)], now=EXPIRES + timedelta(hours=5)
        )
        assert out.instance.state is State.EXPIRED and not store.intents


class TestFireEventIdentity:
    async def test_event_fire_carries_event_uuid_and_occurrence(self, evaluator):
        await register(evaluator, DECISION)
        ev = decision_ev()
        out = await evaluator.evaluate("c1", [ev], now=NOW)
        assert out.intent is not None
        assert str(out.intent.event_id) == ev.id
        assert out.intent.occurrence == f"gen:1:event:{ev.id}"

    async def test_registration_and_clock_fires_mint_stable_uuids(self, evaluator):
        snap = Snapshot(now=NOW, decisions={SUBJECT: "approved"})
        reg = await register(evaluator, DECISION, snapshot=snap, instance_id="r")
        assert reg.intent is not None and isinstance(reg.intent.event_id, UUID)
        await register(evaluator, AT, instance_id="a")
        clock = await evaluator.evaluate("a", [], now=NOW + timedelta(hours=1))
        assert clock.intent is not None and clock.intent.event_id != reg.intent.event_id
        again = await evaluator.evaluate("a", [], now=NOW + timedelta(hours=1))
        assert again.intent == clock.intent

    async def test_stale_at_leaf_does_not_steal_event_attribution(self, evaluator):
        await register(evaluator, {"tag": "all", "children": [DECISION, AT]})
        ev = decision_ev(at=NOW + timedelta(minutes=45))
        out = await evaluator.evaluate("c1", [ev], now=NOW + timedelta(minutes=45))
        assert out.intent is not None and str(out.intent.event_id) == ev.id


class TestReplayFloorIsImmutable:
    async def test_reordered_match_across_evaluate_calls_still_fires(self, evaluator):
        await register(evaluator, DECISION)
        unrelated = decision_ev(position=5, outcome="rejected")
        first = await evaluator.evaluate("c1", [unrelated], now=NOW)
        assert first.instance.state is State.OPEN
        assert dict(first.instance.cursors) == {"goal.decision": 5}
        late = await evaluator.evaluate("c1", [decision_ev(position=3)], now=NOW)
        assert late.instance.state is State.FIRED

    async def test_floor_is_part_of_the_immutable_boundary(self, evaluator):
        await register(evaluator, DECISION, cursors={"goal.decision": 4})
        with pytest.raises(RegistrationRefused):
            await register(evaluator, DECISION, cursors={"goal.decision": 9})


class TestLabels:
    """RFC 0038 S1/S4: a fire joins labels; the registered snapshot is immutable (bu-dhloi1.196).

    ``ConditionEvent`` carries no label yet (producers stamp them under bu-dhloi1.9), and S1 makes
    missing provenance the unknown label, so event and registration-snapshot fires join it into
    the registered label. A clock fire conveys no source data and carries the registered label.
    """

    FIRE = LABEL.join(Label.unknown())

    async def test_registration_fire_joins_the_unknown_label(self, evaluator, store):
        snap = Snapshot(now=NOW, decisions={SUBJECT: "approved"})
        out = await register(evaluator, DECISION, snapshot=snap)
        assert out.instance.label == LABEL
        assert out.intent is not None and out.intent.label == self.FIRE
        assert out.receipt.label == self.FIRE

    async def test_unlabelled_event_fire_carries_unknown_provenance_and_sensitivity(
        self, evaluator, store
    ):
        await register(evaluator, DECISION)
        out = await evaluator.evaluate("c1", [decision_ev()], now=NOW)
        assert out.intent is not None and out.intent.label == self.FIRE
        label = out.intent.label
        assert "world" in label.instr and "world" in label.data
        assert label.sens == CATEGORIES  # all six, as the registered routine alone is not enough
        assert out.receipt.label == self.FIRE
        assert out.instance.label == LABEL  # the stored snapshot itself never changes
        assert label.covers(LABEL)

    async def test_clock_fire_keeps_the_registered_label(self, evaluator, store):
        await register(evaluator, AT)
        out = await evaluator.evaluate("c1", [], now=NOW + timedelta(minutes=31))
        assert out.intent is not None and out.intent.reason.startswith("fired:clock")
        assert out.intent.label == LABEL and out.receipt.label == LABEL

    async def test_non_fire_receipts_carry_the_registered_label(self, evaluator, store):
        await register(evaluator, DECISION, instance_id="b")
        await evaluator.evaluate("b", [], now=EXPIRES + timedelta(minutes=1))
        await register(evaluator, DECISION, instance_id="c")
        await evaluator.cancel("c", now=NOW)
        assert len(store.receipts) >= 4 and {r.label for r in store.receipts} == {LABEL}

    async def test_reregistering_with_a_different_label_refuses(self, evaluator):
        await register(evaluator, DECISION)
        wider = LABEL.join(Label.unknown())
        with pytest.raises(RegistrationRefused):
            await register(evaluator, DECISION, label=wider)
        narrower = Label(frozenset({"owner"}), frozenset({"owner"}), frozenset())
        with pytest.raises(RegistrationRefused):
            await register(evaluator, DECISION, label=narrower)

    async def test_reregistering_with_the_same_label_is_a_retry(self, evaluator):
        first = await register(evaluator, DECISION)
        again = await register(
            evaluator, DECISION, label=Label(LABEL.instr, LABEL.data, LABEL.sens)
        )
        assert again.instance == first.instance


#: Provenance a producer can stamp on its events (bu-dhloi1.305 PR5a).
OWNER = Label.owner()
TAINTED = Label(frozenset({"owner", "external:mail"}), frozenset({"external:mail"}), frozenset())


class TestLabelledEvidence:
    """A producer may stamp each event with the label of the row it came from.

    A data fire then joins the labels of the events that hold its true leaves, instead of the
    unknown label. Every true leaf must be held by a labelled event of the firing batch (or the
    labelled registration snapshot); otherwise the fire joins the unknown label exactly as an
    unlabelled producer's does today.
    """

    FIRE = LABEL.join(Label.unknown())

    async def test_a_labelled_event_fire_joins_its_label_not_the_unknown_one(self, evaluator):
        await register(evaluator, DECISION)
        out = await evaluator.evaluate("c1", [decision_ev(label=OWNER)], now=NOW)
        assert out.intent is not None and out.intent.label == LABEL.join(OWNER)
        assert out.receipt.label == LABEL.join(OWNER)
        assert not out.intent.label.covers(Label.unknown())

    async def test_every_true_leaf_joins_the_label_of_the_event_holding_it(self, evaluator):
        both = {"tag": "all", "children": [DECISION, INPUT]}
        await register(evaluator, both)
        batch = [decision_ev(label=OWNER), input_ev(position=2, label=TAINTED)]
        out = await evaluator.evaluate("c1", batch, now=NOW)
        assert out.intent is not None and out.intent.label == LABEL.join(OWNER).join(TAINTED)

    async def test_a_leaf_held_only_by_an_earlier_batch_joins_the_unknown_label(self, evaluator):
        both = {"tag": "all", "children": [DECISION, INPUT]}
        await register(evaluator, both)
        await evaluator.evaluate("c1", [input_ev(label=OWNER)], now=NOW)
        out = await evaluator.evaluate("c1", [decision_ev(position=2, label=OWNER)], now=NOW)
        assert out.intent is not None and out.intent.label == self.FIRE

    async def test_one_unlabelled_event_holding_a_leaf_joins_the_unknown_label(self, evaluator):
        both = {"tag": "all", "children": [DECISION, INPUT]}
        await register(evaluator, both)
        batch = [decision_ev(label=OWNER), input_ev(position=2)]
        out = await evaluator.evaluate("c1", batch, now=NOW)
        assert out.intent is not None and out.intent.label == self.FIRE

    async def test_an_event_holding_no_true_leaf_adds_nothing(self, evaluator):
        await register(evaluator, DECISION)
        batch = [
            decision_ev(label=OWNER),
            decision_ev(position=2, outcome="rejected", label=TAINTED),
        ]
        out = await evaluator.evaluate("c1", batch, now=NOW)
        assert out.intent is not None and out.intent.label == LABEL.join(OWNER)

    async def test_a_labelled_registration_snapshot_fires_with_its_label(self, evaluator):
        snap = Snapshot(now=NOW, decisions={SUBJECT: "approved"}, label=OWNER)
        out = await register(evaluator, DECISION, snapshot=snap)
        assert out.intent is not None and out.intent.label == LABEL.join(OWNER)

    async def test_a_retry_carries_its_snapshot_label_only_if_that_snapshot_holds_it(
        self, evaluator
    ):
        both = {"tag": "all", "children": [DECISION, INPUT]}
        decided = Snapshot(now=NOW, decisions={SUBJECT: "approved"}, label=OWNER)
        assert (await register(evaluator, both, snapshot=decided)).intent is None
        replied = Snapshot(now=NOW, inputs=frozenset({BINDING}), label=OWNER)
        out = await register(evaluator, both, snapshot=replied)
        # The decision leaf latched from the first snapshot, which this label does not cover.
        assert out.intent is not None and out.intent.label == self.FIRE
        await register(evaluator, both, instance_id="c2", snapshot=decided)
        full = Snapshot(
            now=NOW, decisions={SUBJECT: "approved"}, inputs=frozenset({BINDING}), label=OWNER
        )
        out = await register(evaluator, both, instance_id="c2", snapshot=full)
        assert out.intent is not None and out.intent.label == LABEL.join(OWNER)

    async def test_an_unlabelled_non_goal_producer_keeps_the_unknown_label(self, evaluator):
        """A perception producer that stamps nothing behaves exactly as before."""
        await register(evaluator, TRANSITION)
        out = await evaluator.evaluate("c1", [transition_ev()], now=NOW)
        assert out.intent is not None and out.intent.label == self.FIRE
        assert out.receipt.label == self.FIRE

    # N1: a clock that completes a predicate whose data leaves latched earlier must not launder
    # them. all(owner_input, at(T)) fed a tainted reply before T fires at T on the clock.
    LATER = NOW + timedelta(minutes=31)
    INPUT_AT = {"tag": "all", "children": [INPUT, AT]}

    async def test_a_clock_fire_over_a_latched_data_leaf_joins_the_unknown_label(self, evaluator):
        await register(evaluator, self.INPUT_AT)
        assert (await evaluator.evaluate("c1", [input_ev(label=TAINTED)], now=NOW)).intent is None
        out = await evaluator.evaluate("c1", [], now=self.LATER)
        assert out.intent is not None and out.intent.reason.startswith("fired:clock")
        assert out.intent.label == self.FIRE and out.receipt.label == self.FIRE

    @pytest.mark.parametrize("held", [OWNER, TAINTED], ids=["owner", "tainted"])
    async def test_a_clock_fire_joins_the_batch_events_holding_its_data_leaves(
        self, evaluator, held
    ):
        await register(evaluator, self.INPUT_AT)
        await evaluator.evaluate("c1", [input_ev(label=held)], now=NOW)
        out = await evaluator.evaluate("c1", [input_ev(label=held)], now=self.LATER)
        assert out.intent is not None and out.intent.reason.startswith("fired:clock")
        assert out.intent.label == LABEL.join(held)

    async def test_a_clock_fire_with_no_true_data_leaf_keeps_the_registered_label(self, evaluator):
        either = {"tag": "any", "children": [INPUT, AT]}
        await register(evaluator, either)
        out = await evaluator.evaluate("c1", [], now=self.LATER)
        assert out.intent is not None and out.intent.label == LABEL


class TestM7Leaves:
    """REQ-owner-watches-001/002: the extended AST on the one evaluator."""

    @pytest.mark.parametrize("name", M7_OCCURRENCES)
    @pytest.mark.parametrize(
        "flags",
        [{"late": True}, {"retracted": True}, {"record": True}],
        ids=["late", "retracted", "record"],
    )
    async def test_late_retracted_or_record_never_fires(self, evaluator, name, flags):
        leaf, make, _ = M7_OCCURRENCES[name]
        await register(evaluator, leaf)
        out = await evaluator.evaluate("c1", [make(**flags)], now=NOW)
        assert out.instance.state is State.OPEN and out.intent is None

    @pytest.mark.parametrize("name", M7_OCCURRENCES)
    async def test_stale_event_is_never_realtime(self, evaluator, name):
        leaf, make, _ = M7_OCCURRENCES[name]
        await register(evaluator, leaf)
        stale = make(at=NOW - timedelta(minutes=11))
        out = await evaluator.evaluate("c1", [stale], now=NOW)
        assert out.instance.state is State.OPEN and out.intent is None

    @pytest.mark.parametrize("name", M7_OCCURRENCES)
    async def test_future_dated_event_is_ignored_and_does_not_move_the_cursor(
        self, evaluator, name
    ):
        leaf, make, _ = M7_OCCURRENCES[name]
        await register(evaluator, leaf)
        out = await evaluator.evaluate("c1", [make(at=NOW + timedelta(minutes=5))], now=NOW)
        assert out.instance.state is State.OPEN and not out.instance.cursors

    @pytest.mark.parametrize("name", M7_OCCURRENCES)
    async def test_unmeasurable_event_is_unknown_never_true(self, evaluator, name):
        leaf, make, _ = M7_OCCURRENCES[name]
        await register(evaluator, leaf)
        out = await evaluator.evaluate("c1", [make(measurable=False)], now=NOW)
        assert out.instance.result is Tri.UNKNOWN and out.intent is None

    @pytest.mark.parametrize(
        ("op", "want", "read", "fires"),
        [
            ("lt", 100, 99, True),
            ("lt", 100, 100, False),
            ("le", 100, 100, True),
            ("gt", 100, 101, True),
            ("ge", 100, 99.5, False),
            ("eq", 100, 100.0, True),
            ("ne", 100, 100, False),
            ("eq", "ok", "ok", True),
            ("ne", "ok", "bad", True),
            ("eq", True, True, True),
            ("ne", True, True, False),
        ],
    )
    async def test_read_check_compares_typed_values(self, evaluator, op, want, read, fires):
        await register(evaluator, {**READ_CHECK, "op": op, "value": want})
        out = await evaluator.evaluate("c1", [read_ev(value=read)], now=NOW)
        assert (out.intent is not None) is fires

    @pytest.mark.parametrize(
        "read",
        ["50", True, None, [50], {"v": 50}, float("nan")],
        ids=["string", "bool", "null", "list", "object", "nan"],
    )
    async def test_read_check_output_outside_its_schema_is_unknown(self, evaluator, read):
        await register(evaluator, READ_CHECK)
        out = await evaluator.evaluate("c1", [read_ev(value=read)], now=NOW)
        assert out.instance.result is Tri.UNKNOWN and out.intent is None

    async def test_read_check_from_an_untrusted_source_is_unknown_even_when_in_schema(
        self, evaluator
    ):
        await register(evaluator, READ_CHECK)
        out = await evaluator.evaluate("c1", [read_ev(value=50, trusted=False)], now=NOW)
        assert out.instance.result is Tri.UNKNOWN and out.intent is None

    async def test_a_trusted_read_check_after_an_untrusted_one_still_fires(self, evaluator):
        await register(evaluator, READ_CHECK)
        await evaluator.evaluate("c1", [read_ev(1, value=50, trusted=False)], now=NOW)
        out = await evaluator.evaluate("c1", [read_ev(2, value=50)], now=NOW)
        assert out.intent is not None

    async def test_read_check_for_another_field_is_ignored(self, evaluator):
        await register(evaluator, READ_CHECK)
        other = event(
            "read_check",
            "read_check:finance.balance",
            1,
            {"capability": "finance.balance", "field": "pending", "value": 1},
        )
        out = await evaluator.evaluate("c1", [other], now=NOW)
        assert out.instance.result is Tri.FALSE and out.intent is None

    async def test_unknown_read_propagates_through_all_and_any(self, evaluator):
        await register(evaluator, {"tag": "all", "children": [READ_CHECK, DECISION]})
        out = await evaluator.evaluate("c1", [read_ev(value="50"), decision_ev()], now=NOW)
        assert out.instance.result is Tri.UNKNOWN and out.intent is None
        any_out = await register(
            evaluator, {"tag": "any", "children": [READ_CHECK, DECISION]}, instance_id="c2"
        )
        assert any_out.instance.result is Tri.FALSE
        out = await evaluator.evaluate("c2", [decision_ev()], now=NOW)
        assert out.intent is not None

    async def test_a_known_false_leaf_beats_an_unknown_one_in_all(self, evaluator):
        await register(evaluator, {"tag": "all", "children": [READ_CHECK, MODE]})
        out = await evaluator.evaluate(
            "c1", [read_ev(value="50"), mode_ev(change="exited")], now=NOW
        )
        assert out.instance.result is Tri.FALSE

    @pytest.mark.parametrize(
        "leaf",
        [
            {**EXPECTATION, "subject": str(uuid5(NS, "elsewhere"))},
            {**ROUTINE_DEVIATION, "routine_id": str(uuid5(NS, "elsewhere"))},
            {**CONDITION, "source": "finance:other"},
            {**READ_CHECK, "capability": "health.vitals"},
            {**BUNDLE, "bundle_id": "other"},
        ],
        ids=["expectation", "routine", "condition", "read_check", "bundle"],
    )
    async def test_identity_or_source_outside_the_grant_refuses(self, evaluator, store, leaf):
        with pytest.raises(RegistrationRefused):
            await register(evaluator, leaf)
        assert not store.instances

    @pytest.mark.parametrize("name", M7_OCCURRENCES)
    async def test_leaf_event_of_another_tag_does_not_touch_it(self, evaluator, name):
        leaf, _, source = M7_OCCURRENCES[name]
        await register(evaluator, leaf)
        spoof = event("transition", source, 1, {"kind": "owner.place", "subject": PLACE})
        out = await evaluator.evaluate("c1", [spoof], now=NOW)
        assert out.instance.state is State.OPEN and out.intent is None

    @pytest.mark.parametrize("name", M7_OCCURRENCES)
    def test_every_m7_leaf_validates_and_has_a_stable_source(self, name):
        leaf, _, source = M7_OCCURRENCES[name]
        assert validate(leaf) == leaf
        from butlers.conditions.predicate import sources

        assert sources(leaf) == {source}
