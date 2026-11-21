"""The pre-cutover unknown-sender Ignore approval card, without a database (bu-p7glz9).

A newly surfaced unknown sender parks one approval-gated
``identity_ignore_unknown_sender`` action; Approve archives the sender and, for an email
address, adds one global skip rule. These tests drive the park, the approved tool, the
card preview and the identity-ingress hook against in-memory fakes. The real
``park_pending_action`` path is proven against Postgres in
``test_unknown_sender_ignore_card_integration.py``.
"""

from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from butlers.core.approvals_hooks import DecisionDossier, _validate_dossier
from butlers.identity import ResolvedContact
from butlers.modules.approvals.command_contracts import UNKNOWN_SENDER_IGNORE_COMMAND
from butlers.modules.approvals.notifications import render_approval_card
from butlers.modules.approvals.park import ParkAdmission
from butlers.modules.approvals.rollout import ApprovalDeliveryRollout
from butlers.roster_imports import roster_module

cards = roster_module("switchboard", "tools/identity/unknown_sender_cards")
inject = roster_module("switchboard", "tools/identity/inject")

pytestmark = pytest.mark.unit

ENTITY = UUID("01936a00-0000-7000-8000-0000000000e2")


class _Db:
    """The rows the park and the approved tool read and write."""

    def __init__(self, *, metadata: dict[str, Any] | None = None, roles=()) -> None:
        self.state: dict[str, Any] = {}
        self.entity: dict[str, Any] | None = {
            "canonical_name": "Chloe L",
            "roles": list(roles),
            "metadata": {"unidentified": True} if metadata is None else metadata,
        }
        self.rules: list[dict[str, Any]] = []

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        snapshot = copy.deepcopy((self.state, self.entity, self.rules))
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
        if "|| $2" in sql:
            self.entity["metadata"].update(args[1])
            return "UPDATE 1"
        raise AssertionError(f"unexpected execute: {sql}")


def _admission(*, duplicate: bool = False, admitted: bool = False) -> ParkAdmission:
    return ParkAdmission(
        action_id=cards.ignore_action_id(ENTITY),
        intent_id=uuid4() if admitted else None,
        action_key=None,
        admission_mode="single" if admitted else None,
        presentation_key=None,
        cohort_key=None,
        not_before=None,
        duplicate=duplicate,
    )


async def _park(
    db: _Db,
    monkeypatch: pytest.MonkeyPatch,
    *,
    admission: ParkAdmission | None = None,
    rollout: ApprovalDeliveryRollout | None = None,
    channel: str = "email",
    address: str = "chloe@x.com",
    display_name: str | None = "Chloe L",
    subject: str | None = "Re: dinner",
) -> tuple[bool, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    async def park(conn: Any, **kwargs: Any) -> ParkAdmission:
        calls.append(kwargs)
        return admission or _admission()

    async def read_rollout(_conn: Any, **_kwargs: Any) -> ApprovalDeliveryRollout:
        return rollout or ApprovalDeliveryRollout()

    monkeypatch.setattr(cards, "park_pending_action", park)
    monkeypatch.setattr(cards, "read_approval_delivery_rollout", read_rollout)
    pushed = await cards.park_ignore_card(
        db,
        entity_id=ENTITY,
        channel=channel,
        address=address,
        display_name=display_name,
        subject=subject,
    )
    return pushed, calls


def _args(**overrides: Any) -> dict[str, Any]:
    return {
        "entity_id": str(ENTITY),
        "channel": "email",
        "address": "chloe@x.com",
        "display_name": "Chloe L",
        **overrides,
    }


# -- park ---------------------------------------------------------------------------


async def test_the_park_is_one_exact_call_with_a_server_written_dossier(monkeypatch) -> None:
    db = _Db()
    pushed, calls = await _park(db, monkeypatch)

    assert pushed is False  # rollout off: the dashboard has it, the plain text still goes
    (call,) = calls
    assert call["action_id"] == cards.ignore_action_id(ENTITY)
    assert call["deduplication_key"] == f"switchboard.unknown_sender.ignore:{ENTITY}"
    assert call["tool_name"] == "identity_ignore_unknown_sender"
    assert call["tool_args"] == _args()
    assert tuple(call["tool_args"]) == UNKNOWN_SENDER_IGNORE_COMMAND.argument_names
    assert call["origin_butler"] == "switchboard"
    assert call["expires_at"] - call["requested_at"] == timedelta(hours=72)
    assert call["agent_summary"] == "Ignore Chloe L <chloe@x.com>"
    assert "Re: dinner" in call["why"]
    assert "skip rule" in call["why"] and "Unidentified Entities" in call["why"]
    dossier = _validate_dossier(
        raw_why=call["why"],
        raw_evidence=call["evidence"],
        raw_blast_radius=call["blast_radius"],
        raw_reversibility=call["reversibility"],
        require_why=True,
    )
    assert isinstance(dossier, DecisionDossier)
    assert dossier.blast_radius == "self" and dossier.reversibility == "reversible"
    assert db.state[f"identity:unknown_ignore:{ENTITY}"] == _args()


def test_the_action_id_is_stable_per_entity_and_distinct_across_entities() -> None:
    assert cards.ignore_action_id(ENTITY) == cards.ignore_action_id(ENTITY)
    assert cards.ignore_action_id(ENTITY) != cards.ignore_action_id(uuid4())


async def test_a_repeated_park_keeps_the_first_snapshot(monkeypatch) -> None:
    db = _Db()
    await _park(db, monkeypatch)
    pushed, _ = await _park(
        db, monkeypatch, admission=_admission(duplicate=True), display_name="Someone Else"
    )

    assert pushed is False
    assert db.state[f"identity:unknown_ignore:{ENTITY}"] == _args()


@pytest.mark.parametrize(
    ("admitted", "worker", "duplicate", "pushed"),
    [
        (True, True, False, True),
        (True, False, False, False),
        (False, True, False, False),
        (True, True, True, False),
    ],
)
async def test_only_an_admitted_new_card_with_a_live_worker_replaces_the_notice(
    monkeypatch, admitted: bool, worker: bool, duplicate: bool, pushed: bool
) -> None:
    result, _ = await _park(
        _Db(),
        monkeypatch,
        admission=_admission(duplicate=duplicate, admitted=admitted),
        rollout=ApprovalDeliveryRollout(admission_enabled=admitted, worker_enabled=worker),
    )
    assert result is pushed


async def test_a_non_email_dossier_says_no_rule_is_added(monkeypatch) -> None:
    _, (call,) = await _park(
        _Db(),
        monkeypatch,
        channel="telegram",
        address="12345",
        display_name=None,
        subject=None,
    )
    assert call["agent_summary"] == "Ignore 12345"
    assert "No skip rule is added for telegram" in call["why"]
    assert call["evidence"] == [
        {"type": "entity", "ref": str(ENTITY), "note": "Unidentified sender."}
    ]


async def test_sender_controlled_fields_are_neutralised_on_the_card(monkeypatch) -> None:
    _, (call,) = await _park(
        _Db(), monkeypatch, display_name="  [Bank](http://evil)  `x`  ", subject="*urgent*"
    )
    assert call["tool_args"]["display_name"] == "(Bank)(http://evil) x"
    assert "*" not in call["why"] and "`" not in call["why"]


# -- approved tool -----------------------------------------------------------------


async def test_approve_archives_and_adds_one_skip_rule_idempotently(monkeypatch) -> None:
    db = _Db()
    await _park(db, monkeypatch)

    first = await cards.ignore_unknown_sender(db, **_args())
    again = await cards.ignore_unknown_sender(db, **_args())

    assert first == {"status": "applied", "outcome": "ignored", "rule": "created"}
    assert again == {"status": "applied", "outcome": "ignored", "rule": "existing"}
    assert db.entity is not None and db.entity["metadata"]["archived_at"]
    assert [rule["condition"] for rule in db.rules] == [{"address": "chloe@x.com"}]


async def test_a_non_email_sender_is_archived_without_a_rule(monkeypatch) -> None:
    db = _Db()
    await _park(db, monkeypatch, channel="telegram", address="12345", display_name=None)

    receipt = await cards.ignore_unknown_sender(
        db, **_args(channel="telegram", address="12345", display_name=None)
    )

    assert receipt == {"status": "applied", "outcome": "ignored", "rule": "not_applicable"}
    assert db.entity is not None and db.entity["metadata"]["archived_at"]
    assert db.rules == []


@pytest.mark.parametrize(
    ("entity", "error"),
    [
        ({"metadata": {"unidentified": True}, "roles": ["owner"]}, "entity_not_ignorable"),
        (
            {"metadata": {"unidentified": True, "merged_into": str(uuid4())}, "roles": []},
            "entity_not_ignorable",
        ),
        ({"metadata": {}, "roles": []}, "entity_identified"),
        (None, "entity_not_ignorable"),
    ],
)
async def test_owner_merged_gone_or_identified_entities_are_refused(
    monkeypatch, entity: dict[str, Any] | None, error: str
) -> None:
    db = _Db()
    await _park(db, monkeypatch)
    db.entity = None if entity is None else {"canonical_name": "X", **entity}

    assert await cards.ignore_unknown_sender(db, **_args()) == {"error": error}
    assert db.rules == []
    assert db.entity is None or "archived_at" not in db.entity["metadata"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"address": "someone-else@x.com"},
        {"channel": "telegram"},
        {"display_name": "Chloe"},
        {"entity_id": str(uuid4())},
    ],
)
async def test_a_call_switchboard_did_not_park_is_refused(monkeypatch, overrides) -> None:
    db = _Db()
    await _park(db, monkeypatch)
    if "entity_id" in overrides:
        # Another entity: the fake has no snapshot for it, so the tool refuses.
        db.fetchrow = _no_entity_row(db.fetchrow)

    result = await cards.ignore_unknown_sender(db, **_args(**overrides))

    assert result == {"error": "ignore_card_unknown"}
    assert db.rules == []
    assert db.entity is not None and "archived_at" not in db.entity["metadata"]


def _no_entity_row(fetchrow: Any) -> Any:
    async def wrapped(sql: str, *args: Any) -> Any:
        if "FROM public.entities" in sql:
            return None
        return await fetchrow(sql, *args)

    return wrapped


async def test_a_call_without_any_park_is_refused() -> None:
    db = _Db()
    assert await cards.ignore_unknown_sender(db, **_args()) == {"error": "ignore_card_unknown"}
    assert db.entity is not None and "archived_at" not in db.entity["metadata"]


async def test_a_malformed_entity_id_is_refused() -> None:
    assert await cards.ignore_unknown_sender(_Db(), **_args(entity_id="nope")) == {
        "error": "entity_ref_invalid"
    }


# -- card ---------------------------------------------------------------------------


def _card(args: dict[str, Any]) -> Any:
    return render_approval_card(
        {
            "id": cards.ignore_action_id(ENTITY),
            "tool_name": "identity_ignore_unknown_sender",
            "tool_args": args,
            "expires_at": datetime(2026, 11, 24, 9, 0, tzinfo=UTC),
            "why": "Chloe L <chloe@x.com> wrote to you for the first time on email.",
            "blast_radius": "self",
            "reversibility": "reversible",
        },
        origin_butler="switchboard",
    )


def test_the_card_shows_every_argument_and_offers_one_tap_approval() -> None:
    card = _card(_args())

    assert card.offer_decision is True
    lines = card.message.splitlines()
    assert lines[0] == "Approval needed: Ignore this new sender"
    for label, value in (
        ("Name", "Chloe L"),
        ("Address", "chloe@x.com"),
        ("Channel", "email"),
        ("Entity", str(ENTITY)),
        ("Effect", "Archive this sender and skip their future email"),
    ):
        assert any(line.startswith(f"{label}:") and value in line for line in lines), label


def test_a_non_email_card_promises_only_the_archive() -> None:
    card = _card(_args(channel="telegram", address="12345", display_name=None))

    assert card.offer_decision is True
    assert "Name:" not in card.message
    (effect,) = [line for line in card.message.splitlines() if line.startswith("Effect:")]
    assert "Archive this sender" in effect and "skip" not in effect


def test_an_unshown_argument_fails_closed_to_open_only() -> None:
    assert _card({**_args(), "extra": "x"}).offer_decision is False


# -- identity ingress ----------------------------------------------------------------


def _temp_entity() -> ResolvedContact:
    return ResolvedContact(name="Unknown", roles=[], entity_id=ENTITY, is_unidentified=True)


async def _ingress(*, card_fn: Any, notify: AsyncMock, claimed: bool = True) -> Any:
    with (
        patch.object(inject, "resolve_contact_by_channel", AsyncMock(return_value=None)),
        patch.object(inject, "create_temp_contact", AsyncMock(return_value=_temp_entity())),
        patch.object(inject, "_claim_unknown_sender_notification", AsyncMock(return_value=claimed)),
    ):
        return await inject.resolve_and_inject_identity(
            AsyncMock(),
            "email",
            "chloe@x.com",
            display_name="Chloe L",
            subject="Re: dinner",
            notify_owner_fn=notify,
            unknown_sender_card_fn=card_fn,
        )


async def test_the_first_notice_parks_the_card_and_keeps_the_text_while_dark() -> None:
    card_fn, notify = AsyncMock(return_value=False), AsyncMock()

    await _ingress(card_fn=card_fn, notify=notify)

    card_fn.assert_awaited_once_with(
        entity_id=ENTITY,
        channel="email",
        address="chloe@x.com",
        display_name="Chloe L",
        subject="Re: dinner",
    )
    notify.assert_awaited_once()
    assert notify.await_args is not None
    assert notify.await_args.args[0].startswith("New sender on email: Chloe L <chloe@x.com>")


async def test_a_pushed_card_replaces_the_plain_text_notice() -> None:
    card_fn, notify = AsyncMock(return_value=True), AsyncMock()

    await _ingress(card_fn=card_fn, notify=notify)

    card_fn.assert_awaited_once()
    notify.assert_not_awaited()


async def test_a_failed_park_still_sends_the_plain_text_notice() -> None:
    card_fn, notify = AsyncMock(side_effect=RuntimeError("db down")), AsyncMock()

    result = await _ingress(card_fn=card_fn, notify=notify)

    assert result.is_unknown and result.entity_id == ENTITY
    notify.assert_awaited_once()


async def test_a_repeat_sender_parks_nothing_and_sends_nothing() -> None:
    card_fn, notify = AsyncMock(return_value=False), AsyncMock()

    await _ingress(card_fn=card_fn, notify=notify, claimed=False)

    card_fn.assert_not_awaited()
    notify.assert_not_awaited()
