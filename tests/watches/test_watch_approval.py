"""REQ-owner-watches-003: the watch card rides the approvals spine and applies as parked."""

from __future__ import annotations

import copy
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from butlers.conditions import ConditionEvaluator
from butlers.conditions.memory import InMemoryConditionStore
from butlers.modules.approvals.command_contracts import OWNER_WATCH_APPLY_COMMAND
from butlers.perception.model import canonical
from butlers.watches import WatchRefused, WatchService, WatchState
from butlers.watches import approval as cards
from butlers.watches.memory import InMemoryWatchStore
from butlers.watches.model import Action, WatchCard
from tests.watches.test_watches import NOW, PREDICATE, grant_for, snap


class Db:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {}

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, sql: str, *args: Any) -> str:
        assert "INSERT INTO state" in sql
        self.state.setdefault(args[0], copy.deepcopy(args[1]))
        return "INSERT 0 1"

    async def fetchrow(self, sql: str, *args: Any):
        assert "FROM state WHERE key" in sql
        value = self.state.get(args[0])
        return None if value is None else {"value": json.dumps(value)}


@pytest.fixture
def service():
    return WatchService(
        InMemoryWatchStore(), ConditionEvaluator(InMemoryConditionStore()), grant_for
    )


@pytest.fixture
def parked(monkeypatch):
    calls: list[dict[str, Any]] = []

    async def park(conn, **kwargs):
        duplicate = any(c["action_id"] == kwargs["action_id"] for c in calls)
        calls.append(kwargs)
        return SimpleNamespace(duplicate=duplicate, intent_id="i")

    async def rollout(conn, **_):
        return SimpleNamespace(admission_enabled=True, worker_enabled=True)

    monkeypatch.setattr(cards, "park_pending_action", park)
    monkeypatch.setattr(cards, "read_approval_delivery_rollout", rollout)
    return calls


def new_card(service, **spec) -> WatchCard:
    return service.propose_create({"predicate": PREDICATE, **spec}, now=NOW)


class TestParking:
    async def test_the_parked_call_is_exactly_the_card_and_its_digest(self, service, parked):
        card = new_card(service)
        assert await cards.park_watch_card(Db(), card, now=NOW) is True
        (call,) = parked
        assert call["tool_name"] == OWNER_WATCH_APPLY_COMMAND.name
        assert call["tool_args"] == {"card_json": canonical(card.body), "digest": card.digest}
        assert call["origin_butler"] == "switchboard"
        assert call["deduplication_key"] == f"{cards.DEDUP_KEY_PREFIX}{card.digest}"
        assert call["action_id"] == cards.card_action_id(card.digest)
        assert call["expires_at"] - call["requested_at"] == cards.CARD_TTL

    async def test_a_replayed_proposal_parks_nothing_new_to_push(self, service, parked):
        card, db = new_card(service), Db()
        await cards.park_watch_card(db, card, now=NOW)
        assert await cards.park_watch_card(db, card, now=NOW) is False
        assert len(db.state) == 1

    def test_the_card_shows_closed_vocabulary_and_the_digest_never_prose(self, service):
        card = new_card(service, delivery="right_away")
        text = "\n".join(cards.card_lines(card))
        assert "routine_deviation" in text and "right_away" in text and card.digest[:12] in text
        assert "tells only you" in text
        dossier = cards.card_dossier(card)
        assert dossier["blast_radius"] == "self" and dossier["reversibility"] == "reversible"

    async def test_a_delete_card_is_marked_irreversible(self, service, parked):
        watch = await _applied(service, Db(), parked)
        card = await service.propose_delete(watch.id)
        assert cards.card_dossier(card)["reversibility"] == "irreversible"
        assert any("Delete" in line for line in cards.card_lines(card))


async def _applied(service, db, parked, card=None):
    card = card or new_card(service)
    await cards.park_watch_card(db, card, now=NOW)
    result = await _apply(service, db, card)
    assert result["status"] == "applied", result
    return await service.store.get(card.watch_id)


async def _apply(service, db, card, **overrides):
    args = {"card_json": canonical(card.body), "digest": card.digest, **overrides}
    return await cards.apply_watch_card(
        db,
        service_for=lambda: service,
        snapshot_for=lambda now: snap(now),
        now=NOW,
        **args,
    )


class TestApplying:
    async def test_approving_the_parked_card_creates_the_watch_as_the_owner(self, service, parked):
        watch = await _applied(service, Db(), parked)
        assert watch is not None and watch.state is WatchState.ACTIVE
        assert watch.approval_digest == new_card_digest(watch)

    async def test_a_replayed_approval_changes_nothing(self, service, parked):
        db, card = Db(), new_card(service)
        first = await _applied(service, db, parked, card)
        assert (await _apply(service, db, card))["status"] == "applied"
        assert await service.store.get(card.watch_id) == first

    async def test_a_card_that_was_not_parked_is_refused(self, service, parked):
        card = new_card(service)
        assert await _apply(service, Db(), card) == {"error": "watch_card_unknown"}
        assert await service.store.get(card.watch_id) is None

    async def test_a_tampered_card_is_refused_before_anything_is_read(self, service, parked):
        db, card = Db(), new_card(service)
        await cards.park_watch_card(db, card, now=NOW)
        other = replace(card, delivery="right_away")
        tampered = await _apply(service, db, card, card_json=canonical(other.body))
        assert tampered == {"error": "watch_card_digest_mismatch"}
        assert await service.store.get(card.watch_id) is None

    async def test_a_digest_that_is_not_the_bodys_is_refused(self, service, parked):
        db, card = Db(), new_card(service)
        await cards.park_watch_card(db, card, now=NOW)
        assert await _apply(service, db, card, digest="0" * 64) == {
            "error": "watch_card_digest_mismatch"
        }

    async def test_a_noncanonical_spelling_of_the_card_is_refused(self, service, parked):
        db, card = Db(), new_card(service)
        await cards.park_watch_card(db, card, now=NOW)
        spaced = json.dumps(card.body, sort_keys=True)  # same card, different bytes
        assert spaced != canonical(card.body)
        assert await _apply(service, db, card, card_json=spaced) == {
            "error": "watch_card_digest_mismatch"
        }

    @pytest.mark.parametrize("text", ["not json", "[]", "{}", '{"action":"create"}'])
    async def test_garbage_is_refused(self, service, text, parked):
        db, card = Db(), new_card(service)
        assert await _apply(service, db, card, card_json=text) == {"error": "watch_card_invalid"}

    async def test_without_production_providers_the_apply_refuses_and_changes_nothing(
        self, service, parked
    ):
        db, card = Db(), new_card(service)
        await cards.park_watch_card(db, card, now=NOW)
        result = await cards.apply_watch_card(
            db,
            card_json=canonical(card.body),
            digest=card.digest,
            service_for=cards.PROVIDERS.service_for,
            snapshot_for=cards.PROVIDERS.snapshot_for,
            now=NOW,
        )
        assert result == {"error": "owner_watches_unavailable"}
        assert await service.store.get(card.watch_id) is None

    async def test_a_grant_that_changed_since_the_card_is_refused(self, service, parked):
        db, card = Db(), new_card(service)
        await cards.park_watch_card(db, card, now=NOW)
        stale = replace(card, grant_digest="f" * 64)
        db.state[f"{cards.SNAPSHOT_KEY_PREFIX}{stale.digest}"] = {
            "card_json": canonical(stale.body),
            "digest": stale.digest,
        }
        result = await _apply(service, db, stale)
        assert result["error"].startswith("watch_card_refused")
        assert await service.store.get(card.watch_id) is None


def new_card_digest(watch) -> str:
    return watch.approval_digest


class TestCardRoundTrip:
    def test_every_action_round_trips_through_its_body(self, service):
        card = new_card(service)
        assert WatchCard.from_body(json.loads(canonical(card.body))) == card

    @pytest.mark.parametrize("field", ["action", "delivery", "extra"])
    def test_a_body_that_is_not_exactly_a_card_is_refused(self, service, field):
        body = json.loads(canonical(new_card(service).body))
        body[field] = "nonsense" if field != "extra" else 1
        with pytest.raises(WatchRefused):
            WatchCard.from_body(body)

    def test_a_missing_field_is_refused(self, service):
        body = json.loads(canonical(new_card(service).body))
        del body["sources"]
        with pytest.raises(WatchRefused):
            WatchCard.from_body(body)
        assert Action.CREATE  # the enum the body is checked against
