"""REQ-owner-watches-003/004: the owner watch lifecycle on the one evaluator, no model calls."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

import pytest

from butlers.conditions import ConditionEvaluator, ConditionEvent, Grant, Snapshot, State
from butlers.conditions.memory import InMemoryConditionStore
from butlers.watches import (
    CapExceeded,
    OwnerApproval,
    WatchConflict,
    WatchRefused,
    WatchService,
    WatchState,
)
from butlers.watches.memory import InMemoryWatchStore
from butlers.watches.model import LIFETIME, MAX_ACTIVE, Action

NS = UUID("22222222-2222-2222-2222-222222222222")
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
ROUTINE = str(uuid5(NS, "routine"))
SOURCE = "perception:expect.window"
PREDICATE = {"tag": "routine_deviation", "routine_id": ROUTINE}
BLIND_SOURCES = frozenset({SOURCE})


DEFAULT_SOURCES = frozenset({SOURCE, "condition:finance:bill-overdue"})
GRANT = {"sources": DEFAULT_SOURCES, "subjects": frozenset({ROUTINE})}


@pytest.fixture(autouse=True)
def _reset_grant():
    GRANT.update(sources=DEFAULT_SOURCES, subjects=frozenset({ROUTINE}))


def grant_for(watch_id: str) -> Grant:
    return Grant(goal_id=watch_id, sources=GRANT["sources"], subjects=GRANT["subjects"])


@pytest.fixture
def conditions():
    return InMemoryConditionStore()


@pytest.fixture
def store():
    return InMemoryWatchStore()


@pytest.fixture
def service(store, conditions):
    return WatchService(store, ConditionEvaluator(conditions), grant_for)


def snap(now=NOW, **kw):
    return Snapshot(now=now, **kw)


def approve(card):
    return OwnerApproval(card_digest=card.digest, origin="owner")


async def create(service, *, now=NOW, predicate=None, **spec):
    card = service.propose_create({"predicate": predicate or PREDICATE, **spec}, now=now)
    return card, await service.approve(card, approve(card), snap(now))


def missed(position=1, *, at=NOW, routine=ROUTINE):
    return ConditionEvent(
        id=str(uuid5(NS, f"missed:{position}")),
        source=SOURCE,
        position=position,
        at=at,
        tag="routine_deviation",
        data={"routine_id": routine, "deviation": "missed"},
    )


class TestAuthoring:
    async def test_an_approved_card_registers_one_instance_with_a_receipt(
        self, service, conditions
    ):
        card, watch = await create(service)
        assert watch.state is WatchState.ACTIVE and watch.version == 1
        assert watch.delivery == "digest"  # the default
        assert watch.approval_digest == card.digest
        assert watch.expires_at == NOW + LIFETIME
        assert list(conditions.instances) == [watch.instance_id]
        assert [r.kind for r in conditions.receipts] == ["register"]

    async def test_a_proposal_alone_installs_nothing(self, service, store, conditions):
        service.propose_create({"predicate": PREDICATE}, now=NOW)
        assert not store.watches and not conditions.instances

    @pytest.mark.parametrize(
        "extra",
        [
            {"recipient": "someone@example.test"},
            {"url": "https://example.test/hook"},
            {"template": "Hello {name}"},
            {"action": "send"},
        ],
    )
    async def test_a_spec_with_a_recipient_url_or_template_refuses(self, service, extra):
        with pytest.raises(WatchRefused):
            service.propose_create({"predicate": PREDICATE, **extra}, now=NOW)

    @pytest.mark.parametrize(
        "spec",
        [
            {},
            {"predicate": PREDICATE, "delivery": "sms"},
            {"predicate": PREDICATE, "categories": ["astrology"]},
            {"predicate": PREDICATE, "categories": "health"},
            {"predicate": "when the routine is missed"},
            {"predicate": {"tag": "sql", "query": "select 1"}},
            {"predicate": {"tag": "routine_deviation", "routine_id": str(uuid5(NS, "other"))}},
        ],
    )
    async def test_a_malformed_or_out_of_grant_spec_refuses(self, service, spec):
        with pytest.raises(WatchRefused):
            service.propose_create(spec, now=NOW)

    async def test_only_the_owner_approves_and_only_this_card(self, service, store):
        card = service.propose_create({"predicate": PREDICATE}, now=NOW)
        other = service.propose_create({"predicate": PREDICATE, "delivery": "right_away"}, now=NOW)
        for bad in (
            OwnerApproval(card_digest=card.digest, origin="model"),
            OwnerApproval(card_digest=card.digest, origin="world"),
            OwnerApproval(card_digest=other.digest, origin="owner"),
        ):
            with pytest.raises(WatchRefused):
                await service.approve(card, bad, snap())
        assert not store.watches

    async def test_expiry_is_ninety_days_from_approval_not_from_the_proposal(self, service):
        card = service.propose_create({"predicate": PREDICATE}, now=NOW)
        approved_at = NOW + timedelta(days=3)
        watch = await service.approve(card, approve(card), snap(approved_at))
        assert watch.expires_at == approved_at + LIFETIME

    async def test_the_card_binds_the_lifetime_not_a_proposal_time_expiry(self, service):
        early = service.propose_create({"predicate": PREDICATE}, now=NOW, watch_id="w")
        late = service.propose_create(
            {"predicate": PREDICATE}, now=NOW + timedelta(days=1), watch_id="w"
        )
        assert early.digest == late.digest and early.body["lifetime_days"] == 90

    async def test_origin_is_required_on_an_approval(self):
        with pytest.raises(TypeError):
            OwnerApproval(card_digest="x")  # type: ignore[call-arg]

    @pytest.mark.parametrize(
        "mutate",
        [
            {"sources": DEFAULT_SOURCES | {"perception:owner.mode"}},
            {"sources": frozenset({SOURCE})},
            {"subjects": frozenset({ROUTINE, "someone-else"})},
        ],
        ids=["source added", "source removed", "subject widened"],
    )
    async def test_a_grant_changed_since_the_card_refuses_create(self, service, store, mutate):
        card = service.propose_create({"predicate": PREDICATE}, now=NOW)
        GRANT.update(mutate)
        with pytest.raises(WatchRefused):
            await service.approve(card, approve(card), snap())
        assert not store.watches

    async def test_a_grant_changed_since_the_card_refuses_renew_and_resume(
        self, service, conditions
    ):
        _, watch = await create(service)
        later = watch.expires_at - timedelta(days=7)
        renew = await service.propose_renew(watch.id, now=later)
        await service.pause(watch.id, origin="owner", now=NOW)
        resume = await service.propose_update(watch.id, resume=True)
        GRANT.update(sources=DEFAULT_SOURCES | {"perception:owner.mode"})
        registered = len(conditions.instances)
        for card in (renew, resume):
            with pytest.raises(WatchRefused):
                await service.approve(card, approve(card), snap(later))
        assert len(conditions.instances) == registered

    async def test_the_grant_is_rechecked_at_the_register_seam(self, service):
        _, watch = await create(service)
        GRANT.update(sources=frozenset({SOURCE}))
        with pytest.raises(WatchRefused):
            await service._register(watch, snap())

    async def test_a_replayed_approval_creates_one_watch_and_one_instance(
        self, service, store, conditions
    ):
        card, first = await create(service)
        again = await service.approve(card, approve(card), snap())
        assert again == first and len(store.watches) == 1 and len(conditions.instances) == 1

    async def test_a_watch_id_cannot_be_taken_over_by_another_card(self, service):
        card, _ = await create(service)
        clash = service.propose_create(
            {"predicate": PREDICATE, "delivery": "right_away"}, now=NOW, watch_id=card.watch_id
        )
        with pytest.raises(WatchRefused):
            await service.approve(clash, approve(clash), snap())

    async def test_a_failed_registration_leaves_no_live_watch(self, store, conditions):
        class Broken(WatchService):
            async def _register(self, watch, snapshot):
                raise OSError("evaluator down")

        service = Broken(store, ConditionEvaluator(conditions), grant_for)
        card = service.propose_create({"predicate": PREDICATE}, now=NOW)
        with pytest.raises(OSError):
            await service.approve(card, approve(card), snap())
        assert [w.state for w in store.watches.values()] == [WatchState.DELETED]


class TestCap:
    async def test_the_fifty_first_watch_refuses_and_registers_nothing(self, service, conditions):
        for _ in range(MAX_ACTIVE):
            await create(service)
        registered = len(conditions.instances)
        card = service.propose_create({"predicate": PREDICATE}, now=NOW)
        with pytest.raises(CapExceeded):
            await service.approve(card, approve(card), snap())
        assert len(conditions.instances) == registered == MAX_ACTIVE

    async def test_concurrent_approvals_cannot_exceed_the_cap(self, store, conditions):
        service = WatchService(store, ConditionEvaluator(conditions), grant_for, cap=3)
        cards = [service.propose_create({"predicate": PREDICATE}, now=NOW) for _ in range(6)]
        results = await asyncio.gather(
            *(service.approve(c, approve(c), snap()) for c in cards), return_exceptions=True
        )
        assert sum(isinstance(r, CapExceeded) for r in results) == 3
        active = [w for w in store.watches.values() if w.state is WatchState.ACTIVE]
        assert len(active) == 3

    async def test_a_deleted_or_paused_watch_frees_its_slot(self, store, conditions):
        service = WatchService(store, ConditionEvaluator(conditions), grant_for, cap=2)
        _, first = await create(service)
        _, second = await create(service)
        await service.pause(first.id, origin="owner", now=NOW)
        await create(service)
        delete = await service.propose_delete(second.id)
        await service.approve(delete, approve(delete), snap())
        await create(service)
        assert sum(w.state is WatchState.ACTIVE for w in store.watches.values()) == 2

    async def test_resuming_counts_against_the_cap(self, store, conditions):
        service = WatchService(store, ConditionEvaluator(conditions), grant_for, cap=1)
        _, first = await create(service)
        await service.pause(first.id, origin="owner", now=NOW)
        await create(service)
        card = await service.propose_update(first.id, resume=True)
        with pytest.raises(CapExceeded):
            await service.approve(card, approve(card), snap())
        # The refused resume leaves PAUSED with no condition open for the generation it tried.
        assert (await store.get(first.id)).state is WatchState.PAUSED
        assert conditions.instances[f"{first.id}:2"].state is State.CANCELLED


class TestExpiryAndRenewal:
    async def test_no_renewal_card_before_the_last_seven_days(self, service):
        _, watch = await create(service)
        with pytest.raises(WatchRefused):
            await service.propose_renew(watch.id, now=watch.expires_at - timedelta(days=8))
        assert await service.renewal_due(watch.expires_at - timedelta(days=8)) == []

    async def test_a_renewal_in_the_window_extends_expiry_with_a_new_generation(
        self, service, conditions
    ):
        _, watch = await create(service)
        later = watch.expires_at - timedelta(days=7)
        assert [w.id for w in await service.renewal_due(later)] == [watch.id]
        card = await service.propose_renew(watch.id, now=later)
        renewed = await service.approve(card, approve(card), snap(later))
        assert renewed.expires_at == later + LIFETIME and renewed.generation == 2
        assert conditions.instances[watch.instance_id].state is State.CANCELLED
        assert conditions.instances[renewed.instance_id].state is State.OPEN
        assert await service.renewal_due(later) == []

    async def test_a_lost_renewal_cas_cancels_the_new_generation(self, store, conditions):
        class Racing(InMemoryWatchStore):
            async def write(self, watch, *, expected_version, cap):
                if watch.generation == 2 and expected_version is not None:
                    raise WatchConflict(watch.id)
                return await super().write(watch, expected_version=expected_version, cap=cap)

        service = WatchService(Racing(), ConditionEvaluator(conditions), grant_for)
        _, watch = await create(service)
        later = watch.expires_at - timedelta(days=7)
        card = await service.propose_renew(watch.id, now=later)
        with pytest.raises(WatchConflict):
            await service.approve(card, approve(card), snap(later))
        assert conditions.instances[f"{watch.id}:2"].state is State.CANCELLED
        assert conditions.instances[watch.instance_id].state is State.OPEN  # the old one stays

    async def test_an_unrenewed_watch_expires_and_keeps_its_record(
        self, service, store, conditions
    ):
        _, watch = await create(service)
        expired = await service.expire_due(watch.expires_at)
        assert [w.state for w in expired] == [WatchState.EXPIRED]
        assert watch.id in store.watches  # nothing deleted
        assert conditions.instances[watch.instance_id].state is State.EXPIRED
        assert conditions.receipts  # receipts stay

    async def test_an_expired_watch_cannot_be_renewed_or_edited(self, service):
        _, watch = await create(service)
        await service.expire_due(watch.expires_at)
        for propose in (service.propose_delete, service.propose_update):
            with pytest.raises(WatchRefused):
                await propose(watch.id)

    async def test_a_renewal_card_cannot_be_replayed_to_extend_again(self, service):
        _, watch = await create(service)
        later = watch.expires_at - timedelta(days=7)
        card = await service.propose_renew(watch.id, now=later)
        first = await service.approve(card, approve(card), snap(later))
        assert await service.approve(card, approve(card), snap(later)) == first


class TestNeverAndDelete:
    async def test_never_pauses_without_deleting_observations(self, service, store, conditions):
        _, watch = await create(service)
        paused = await service.pause(watch.id, origin="owner", now=NOW)
        assert paused.state is WatchState.PAUSED and watch.id in store.watches
        assert conditions.instances[watch.instance_id].state is State.CANCELLED
        assert conditions.receipts

    async def test_a_paused_watch_never_fires(self, service, conditions):
        _, watch = await create(service)
        await service.pause(watch.id, origin="owner", now=NOW)
        out = await service.evaluator.evaluate(watch.instance_id, [missed()], now=NOW)
        assert out.intent is None and not conditions.intents

    async def test_only_the_owner_pauses(self, service):
        _, watch = await create(service)
        with pytest.raises(WatchRefused):
            await service.pause(watch.id, origin="model", now=NOW)

    async def test_resume_registers_a_new_generation_on_an_exact_card(self, service, conditions):
        _, watch = await create(service)
        await service.pause(watch.id, origin="owner", now=NOW)
        card = await service.propose_update(watch.id, resume=True)
        resumed = await service.approve(card, approve(card), snap())
        assert resumed.state is WatchState.ACTIVE and resumed.generation == 2
        assert conditions.instances[resumed.instance_id].state is State.OPEN

    async def test_delete_stops_the_instance_and_keeps_the_record(self, service, store, conditions):
        _, watch = await create(service)
        card = await service.propose_delete(watch.id)
        deleted = await service.approve(card, approve(card), snap())
        assert deleted.state is WatchState.DELETED and watch.id in store.watches
        assert conditions.instances[watch.instance_id].state is State.CANCELLED

    async def test_delete_commits_before_it_cancels_and_a_replay_finishes_a_missed_cancel(
        self, conditions
    ):
        seen = []

        class Watching(InMemoryWatchStore):
            async def write(self, watch, *, expected_version, cap):
                if watch.state is WatchState.DELETED:
                    seen.append(conditions.instances[watch.instance_id].state)
                return await super().write(watch, expected_version=expected_version, cap=cap)

        service = WatchService(Watching(), ConditionEvaluator(conditions), grant_for)
        _, watch = await create(service)
        card = await service.propose_delete(watch.id)
        await service.approve(card, approve(card), snap())
        assert seen == [State.OPEN]  # the commit came first
        assert conditions.instances[watch.instance_id].state is State.CANCELLED

    async def test_a_stale_card_refuses(self, service):
        _, watch = await create(service)
        update = await service.propose_update(watch.id, delivery="right_away")
        delete = await service.propose_delete(watch.id)
        await service.approve(update, approve(update), snap())
        with pytest.raises(WatchRefused):
            await service.approve(delete, approve(delete), snap())

    async def test_a_card_cannot_change_the_boundary(self, service):
        _, watch = await create(service)
        card = await service.propose_update(watch.id, delivery="right_away")
        forged = type(card)(**{**card.__dict__, "predicate": {**PREDICATE, "routine_id": ROUTINE}})
        forged = type(card)(**{**forged.__dict__, "categories": ("health",)})
        with pytest.raises(WatchRefused):
            await service.approve(forged, approve(forged), snap())


class TestDeliveryDefaults:
    async def test_digest_by_default_and_right_away_only_when_chosen(self, service):
        _, default = await create(service)
        _, chosen = await create(service, delivery="right_away")
        assert (default.delivery, chosen.delivery) == ("digest", "right_away")

    async def test_the_card_names_the_delivery_the_owner_approves(self, service):
        card = service.propose_create({"predicate": PREDICATE, "delivery": "right_away"}, now=NOW)
        assert card.body["delivery"] == "right_away" and card.action is Action.CREATE


class TestBlindAndFire:
    async def test_an_unmeasurable_source_shows_blind_never_healthy(self, service):
        card = service.propose_create({"predicate": PREDICATE}, now=NOW)
        watch = await service.approve(card, approve(card), snap(unmeasurable=BLIND_SOURCES))
        view = await service.view(watch.id)
        assert view.blind is True

    async def test_a_measured_watch_is_not_blind(self, service):
        _, watch = await create(service)
        assert (await service.view(watch.id)).blind is False

    async def test_a_matching_event_fires_once_without_a_model(self, service, conditions):
        _, watch = await create(service)
        first = await service.evaluator.evaluate(watch.instance_id, [missed()], now=NOW)
        again = await service.evaluator.evaluate(watch.instance_id, [missed(), missed()], now=NOW)
        assert first.intent is not None and again.intent == first.intent
        assert len(conditions.intents) == 1
