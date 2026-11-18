"""The dark ``switchboard.unknown_sender`` confirm source, without a database (bu-g0nsbw).

The source is built and registered by ``switchboard_confirm_sources`` but not composed:
signed confirm stays dark until the RFC 0038 canonical switch (follow-up bu-6hszs3).
These tests drive the snapshot, ``prepare`` and ``apply`` against an in-memory fake of
the Switchboard ``state`` table, the entity row and the ingestion rules, and drive one
Telegram tap end to end through ``ConfirmAuthorityTopology`` with fake delivery.
"""

from __future__ import annotations

import asyncio
import copy
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID, uuid4

import pytest

from butlers.core.confirm_models import Recipient
from butlers.roster_imports import roster_module

cards = roster_module("switchboard", "tools/identity/unknown_sender_cards")

pytestmark = pytest.mark.unit

ENTITY = UUID("01936a00-0000-7000-8000-0000000000e1")
OWNER = UUID("01936a00-0000-7000-8000-0000000000aa")
CONFIRM = UUID("01936a00-0000-7000-8000-0000000000c1")


async def _owner() -> Recipient:
    return Recipient(OWNER, "42", "42", True)


async def _other_owner_chat() -> Recipient:
    return Recipient(OWNER, "43", "43", True)


async def _no_owner() -> None:
    return None


class _Db:
    """The rows the source reads and writes, behind asyncpg-shaped calls."""

    def __init__(self, *, metadata: dict[str, Any] | None = None, name: str = "Chloe L") -> None:
        self.state: dict[str, Any] = {}
        self.entity: dict[str, Any] | None = {
            "canonical_name": name,
            "roles": [],
            "metadata": {"unidentified": True} if metadata is None else metadata,
        }
        self.rules: list[dict[str, Any]] = []
        self.transactions = 0

    # -- pool surface -----------------------------------------------------------------
    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        snapshot = copy.deepcopy((self.state, self.entity, self.rules))
        self.transactions += 1
        try:
            yield
        except BaseException:
            self.state, self.entity, self.rules = snapshot
            raise

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if "FROM state WHERE key" in sql:
            value = self.state.get(args[0])
            return None if value is None else {"value": copy.deepcopy(value)}
        if "INSERT INTO state" in sql:
            if args[0] in self.state:
                return None
            self.state[args[0]] = copy.deepcopy(args[1])
            return {"key": args[0]}
        if "FROM public.entities" in sql:
            assert args[0] == ENTITY
            return None if self.entity is None else copy.deepcopy(self.entity)
        if "FROM switchboard.ingestion_rules" in sql:
            for rule in self.rules:
                if rule["condition"]["address"] == args[0]:
                    return {"id": rule["id"]}
            return None
        if "INSERT INTO switchboard.ingestion_rules" in sql:
            rule = {"id": uuid4(), "condition": args[0], "action": "skip"}
            self.rules.append(rule)
            return {"id": rule["id"]}
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def execute(self, sql: str, *args: Any) -> str:
        assert self.entity is not None and args[0] == ENTITY
        if "- ARRAY['unidentified', 'duplicate_candidate']" in sql:
            self.entity["canonical_name"] = args[1]
            for key in ("unidentified", "duplicate_candidate"):
                self.entity["metadata"].pop(key, None)
            return "UPDATE 1"
        if "|| $2" in sql:
            self.entity["metadata"].update(args[1])
            return "UPDATE 1"
        raise AssertionError(f"unexpected execute: {sql}")


async def _carded(
    db: _Db,
    *,
    channel: str = "email",
    address: str = "chloe@x.com",
    display_name: str | None = "Chloe L",
    subject: str | None = "Re: dinner",
    owner=_owner,
) -> list[tuple[str, dict]]:
    """Run the card step with a recording producer; return its requests."""
    requests: list[tuple[str, dict]] = []

    async def request(key: str, data: dict) -> dict:
        requests.append((key, data))
        return {"status": "accepted", "confirm_id": str(CONFIRM)}

    outcome = await cards.request_card(
        db,
        request=request,
        owner_recipient=owner,
        entity_id=ENTITY,
        channel=channel,
        address=address,
        display_name=display_name,
        subject=subject,
    )
    assert outcome == ("carded" if requests else "recipient_unavailable")
    return requests


# -- the card -------------------------------------------------------------------------


async def test_the_card_shows_name_address_subject_and_offers_two_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_URL", "https://dash.example")
    db = _Db()
    requests = await _carded(db)
    source = cards.build_source(db, owner_recipient=_owner)

    prompt, ref = await source.prepare(*requests[0])

    assert requests == [(str(ENTITY), {"entity_id": str(ENTITY)})]
    assert ref == str(ENTITY)
    assert prompt.card.facts == (
        "New sender on email: Chloe L <chloe@x.com>",
        "Subject: Re: dinner",
        f"Open: https://dash.example/entities/{ENTITY}",
    )
    assert [(o.value, o.label) for o in prompt.options] == [
        ("add_contact", "Add as contact"),
        ("ignore", "Ignore this sender"),
    ]
    assert prompt.on_timeout_value is None  # an unanswered card never invents a choice
    assert prompt.recipient == Recipient(OWNER, "42", "42", True)


async def test_a_repeated_card_step_asks_the_identical_question() -> None:
    """The snapshot is fixed once, so a retried step never asks a second question."""
    db = _Db()
    first = await _carded(db, subject="Re: dinner")
    second = await _carded(db, subject="a different subject later")
    source = cards.build_source(db, owner_recipient=_owner)

    assert first == second
    one, _ = await source.prepare(*first[0])
    two, _ = await source.prepare(*second[0])
    assert one == two
    assert "Subject: Re: dinner" in one.card.facts


async def test_no_card_without_an_owner_recipient() -> None:
    db = _Db()
    assert await _carded(db, owner=_no_owner) == []
    assert db.state == {}


def test_the_source_registers_one_sink_with_the_closed_vocabulary() -> None:
    source = cards.build_source(_Db(), owner_recipient=_owner)

    assert source.sink_name == "switchboard.unknown_sender"
    assert source.values == frozenset({"add_contact", "ignore"})
    assert source.apply is not None


def test_switchboard_confirm_sources_builds_the_source_by_name() -> None:
    from butlers.core.confirm_authority import UNKNOWN_SENDER_SOURCE, switchboard_confirm_sources

    sources = switchboard_confirm_sources(_Db())

    assert list(sources) == [UNKNOWN_SENDER_SOURCE] == ["switchboard.unknown_sender"]


@pytest.mark.parametrize(
    ("key", "data", "code"),
    [
        ("not-a-uuid", {"entity_id": "not-a-uuid"}, "key_invalid"),
        (str(ENTITY), {"entity_id": str(ENTITY), "x": 1}, "data_invalid"),
        (str(ENTITY), {}, "data_invalid"),
    ],
    ids=["bad-key", "extra-data", "no-data"],
)
async def test_prepare_refuses_malformed_requests(key: str, data: dict, code: str) -> None:
    db = _Db()
    await _carded(db)
    with pytest.raises(cards.CardRefused, match=code):
        await cards.build_source(db, owner_recipient=_owner).prepare(key, data)


async def test_prepare_refuses_without_a_snapshot() -> None:
    source = cards.build_source(_Db(), owner_recipient=_owner)
    with pytest.raises(cards.CardRefused, match="not_cardable"):
        await source.prepare(str(ENTITY), {"entity_id": str(ENTITY)})


async def test_prepare_refuses_an_entity_already_settled() -> None:
    db = _Db()
    await _carded(db)
    db.entity = {"canonical_name": "Chloe L", "roles": [], "metadata": {}}
    with pytest.raises(cards.CardRefused, match="not_cardable"):
        await cards.build_source(db, owner_recipient=_owner).prepare(
            str(ENTITY), {"entity_id": str(ENTITY)}
        )


@pytest.mark.parametrize("owner", [_other_owner_chat, _no_owner], ids=["other-chat", "none"])
async def test_prepare_refuses_when_the_owner_recipient_changed(owner) -> None:
    db = _Db()
    await _carded(db)
    with pytest.raises(cards.CardRefused, match="owner_recipient_changed"):
        await cards.build_source(db, owner_recipient=owner).prepare(
            str(ENTITY), {"entity_id": str(ENTITY)}
        )


# -- apply ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "value", "message"),
    [(str(ENTITY), "block", "unregistered"), ("not-a-uuid", "ignore", "ref_invalid")],
    ids=["unregistered-value", "bad-ref"],
)
async def test_apply_refuses_before_any_read(ref: str, value: str, message: str) -> None:
    class _NoPool:
        def acquire(self):
            raise AssertionError("no read before validation")

    with pytest.raises(ValueError, match=message):
        await cards.apply_action(_NoPool(), CONFIRM, ref, value)


async def test_add_contact_confirms_the_entity_and_is_idempotent() -> None:
    db = _Db()
    await _carded(db)

    first = await cards.apply_action(db, CONFIRM, str(ENTITY), "add_contact")
    again = await cards.apply_action(db, CONFIRM, str(ENTITY), "add_contact")

    assert first == again == {"status": "applied", "outcome": "added"}
    assert db.entity == {"canonical_name": "Chloe L", "roles": [], "metadata": {}}
    assert db.rules == []


async def test_add_contact_names_a_placeholder_entity_by_its_address() -> None:
    db = _Db(name="Unknown (email chloe@x.com)")
    await _carded(db, display_name=None)

    await cards.apply_action(db, CONFIRM, str(ENTITY), "add_contact")

    assert db.entity is not None and db.entity["canonical_name"] == "chloe@x.com"


async def test_add_contact_on_an_already_identified_entity_changes_nothing() -> None:
    db = _Db(metadata={})
    await _carded(db)

    receipt = await cards.apply_action(db, CONFIRM, str(ENTITY), "add_contact")

    assert receipt == {"status": "applied", "outcome": "already_identified"}
    assert db.entity is not None and db.entity["metadata"] == {}


async def test_ignore_archives_the_entity_and_adds_one_skip_rule_idempotently() -> None:
    db = _Db()
    await _carded(db, address="Chloe@X.com")

    first = await cards.apply_action(db, CONFIRM, str(ENTITY), "ignore")
    again = await cards.apply_action(db, CONFIRM, str(ENTITY), "ignore")
    other_card = await cards.apply_action(db, uuid4(), str(ENTITY), "ignore")

    assert first == again == {"status": "applied", "outcome": "ignored", "rule": "created"}
    assert other_card == {"status": "applied", "outcome": "ignored", "rule": "existing"}
    assert [r["condition"] for r in db.rules] == [{"address": "chloe@x.com"}]
    assert db.entity is not None
    assert db.entity["metadata"]["unidentified"] is True  # still reviewable, just hidden
    assert db.entity["metadata"]["archived_at"]


async def test_ignore_for_a_non_email_sender_archives_without_a_rule() -> None:
    db = _Db()
    await _carded(db, channel="telegram", address="12345")

    receipt = await cards.apply_action(db, CONFIRM, str(ENTITY), "ignore")

    assert receipt == {"status": "applied", "outcome": "ignored", "rule": "not_applicable"}
    assert db.rules == []


async def test_a_gone_entity_records_a_receipt_and_writes_nothing() -> None:
    db = _Db()
    await _carded(db)
    db.entity = None

    receipt = await cards.apply_action(db, CONFIRM, str(ENTITY), "ignore")

    assert receipt == {"status": "applied", "outcome": "already_gone"}
    assert db.rules == []


async def test_a_failed_write_rolls_back_with_no_receipt() -> None:
    db = _Db()
    await _carded(db)

    async def broken(sql: str, *args: Any) -> dict[str, Any] | None:
        if "INSERT INTO switchboard.ingestion_rules" in sql:
            raise RuntimeError("rules unavailable")
        return await _Db.fetchrow(db, sql, *args)

    db.fetchrow = broken  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await cards.apply_action(db, CONFIRM, str(ENTITY), "ignore")

    assert not any(key.startswith(cards.RECEIPT_KEY_PREFIX) for key in db.state)
    assert db.entity is not None and "archived_at" not in db.entity["metadata"]


# -- the callback path, end to end with fakes -----------------------------------------


class _FakeService:
    """The confirm service surface the topology composes, recording created rows."""

    def __init__(self) -> None:
        self._sinks: dict[str, Any] = {}
        self.rows: dict[UUID, dict[str, Any]] = {}

    async def create(self, producer, key, prompt, *, sink=None, sink_ref=None, **_):
        for row in self.rows.values():
            if row["key"] == key:
                return row
        row = {"id": uuid4(), "key": key, "prompt": prompt, "sink": sink, "sink_ref": sink_ref}
        self.rows[row["id"]] = row
        return row

    async def tap(self, confirm_id: UUID, index: int) -> dict:
        """What the resolver does after a verified cfm1 tap: dispatch the chosen value."""
        row = self.rows[confirm_id]
        value = row["prompt"].options[index].value
        return await self._sinks[row["sink"]].apply(confirm_id, row["sink_ref"], value)


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux kernel-peer transport")
async def test_a_telegram_tap_applies_through_the_composed_topology() -> None:
    """Producer request -> stored card -> tapped option -> protected sink -> receipt."""
    from butlers.core.confirm_authority import (
        UNKNOWN_SENDER_SOURCE,
        ConfirmAuthorityTopology,
        switchboard_confirm_sources,
    )

    db = _Db()
    service = _FakeService()
    # The production builder, with only the owner lookup swapped for the fake.
    sources = switchboard_confirm_sources(db)
    sources[UNKNOWN_SENDER_SOURCE] = cards.build_source(db, owner_recipient=_owner)
    # Kernel Unix-socket path limits: keep the socket directory short.
    with TemporaryDirectory(prefix="cf-") as directory:
        topology = ConfirmAuthorityTopology(
            service,  # type: ignore[arg-type]
            sources=sources,
            socket_directory=Path(directory),
        )
        async with asyncio.timeout(10), topology:
            outcome = await cards.request_card(
                db,
                request=topology.producer(UNKNOWN_SENDER_SOURCE).request,
                owner_recipient=_owner,
                entity_id=ENTITY,
                channel="email",
                address="chloe@x.com",
                display_name="Chloe L",
                subject="Re: dinner",
            )
            (confirm_id,) = service.rows
            first = await service.tap(confirm_id, 1)  # "Ignore this sender"
            retried = await service.tap(confirm_id, 1)

    assert outcome == "carded"
    assert first == retried == {"status": "applied", "outcome": "ignored", "rule": "created"}
    assert len(db.rules) == 1
