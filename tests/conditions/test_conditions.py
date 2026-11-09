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

NS = UUID("11111111-1111-1111-1111-111111111111")
GOAL = str(uuid5(NS, "goal"))
SUBJECT = str(uuid5(NS, "subject"))
EFFECT = str(uuid5(NS, "effect"))
BINDING = str(uuid5(NS, "binding"))
PLACE = str(uuid5(NS, "place"))
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
EXPIRES = NOW + timedelta(hours=2)
SOURCES = frozenset(
    {"goal.decision", "goal.inbox", "goal.effect", "clock", "perception:owner.place"}
)
GRANT = Grant(
    goal_id=GOAL,
    sources=SOURCES,
    decisions=frozenset({SUBJECT}),
    effects=frozenset({EFFECT}),
    bindings=frozenset({BINDING}),
    subjects=frozenset({PLACE}),
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
        **kw,
    )


@pytest.fixture
def store():
    return InMemoryConditionStore()


@pytest.fixture
def evaluator(store):
    return ConditionEvaluator(store)


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
        if name == "transition":
            pytest.skip("a transition is an edge; a snapshot cannot witness it")
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
                snapshot=Snapshot(now=NOW),
                expires_at=EXPIRES,
            )
        with pytest.raises(RegistrationRefused):
            await evaluator.register(
                instance_id="c1",
                predicate=DECISION,
                grant=GRANT,
                goal_id=GOAL,
                snapshot=Snapshot(now=NOW),
                expires_at=NOW + timedelta(days=400),
            )
        with pytest.raises(RegistrationRefused):
            await evaluator.register(
                instance_id="c1",
                predicate=DECISION,
                grant=GRANT,
                goal_id=GOAL,
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
