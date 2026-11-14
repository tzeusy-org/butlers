"""Owner-chat candidate card source, without a database (bu-jv1moa.11, PR4).

REQ-commitment-lifecycle-013 and -014. The card step, the sink transaction, the
receipts and every race run against migrated PostgreSQL and the real confirm
service in ``tests/integration/test_signed_confirms.py``.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest

from butlers.core.confirm_models import Recipient
from butlers.roster_imports import roster_module

cards = roster_module("relationship", "tools/commitment_candidate_cards")

pytestmark = pytest.mark.unit

CANDIDATE = "01936a00-0000-7000-8000-0000000000c1"
OWNER = UUID("01936a00-0000-7000-8000-0000000000aa")
SNAPSHOT: dict[str, Any] = {
    "question": "You promised this in a chat. Track it?",
    "facts": ["To: Sam Rivera"],
    "quotes": ["send you the deck"],
    "recipient": {"entity_id": str(OWNER), "sender_id": "42", "chat_id": "42"},
    "timeout_seconds": 86400,
    "requested_at": "2026-11-14T12:00:00+00:00",
}


class _Pool:
    def __init__(self, row: dict[str, Any] | None, posture: str = "active") -> None:
        self.row = row
        self.posture = posture

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        return self.row

    async def fetchval(self, sql: str, *args: Any) -> Any:
        assert "posture" in sql
        return self.posture


def _row(state: str = "pending", snapshot: dict[str, Any] | None = SNAPSHOT) -> dict[str, Any]:
    return {
        "state": state,
        "counterparty_entity_id": UUID("01936a00-0000-7000-8000-0000000000a1"),
        "card_snapshot": None if snapshot is None else json.dumps(snapshot),
    }


async def _owner() -> Recipient:
    return Recipient(OWNER, "42", "42", True)


async def _other_owner_chat() -> Recipient:
    return Recipient(OWNER, "43", "43", True)


async def _no_owner() -> None:
    return None


async def test_prepare_rebuilds_the_same_prompt_from_the_snapshot_every_time() -> None:
    source = cards.build_source(_Pool(_row()), owner_recipient=_owner)

    first, ref = await source.prepare(CANDIDATE, {"candidate_id": CANDIDATE})
    second, _ = await source.prepare(CANDIDATE, {"candidate_id": CANDIDATE})

    assert first == second
    assert ref == CANDIDATE
    assert [o.value for o in first.options] == ["track", "done", "ignore"]
    assert first.on_timeout_value is None  # an unanswered card never invents a choice
    assert first.recipient == Recipient(OWNER, "42", "42", True)
    assert first.timeout_seconds == 86400
    assert first.card.quotes == ("send you the deck",)


def test_the_source_registers_one_sink_with_the_closed_vocabulary() -> None:
    source = cards.build_source(_Pool(None), owner_recipient=_owner)

    assert source.sink_name == "relationship.commitment_candidate"
    assert source.values == frozenset({"track", "done", "ignore"})
    assert source.apply is not None


@pytest.mark.parametrize(
    ("key", "data", "state", "has_snapshot", "posture", "code"),
    [
        ("not-a-uuid", {"candidate_id": "not-a-uuid"}, "pending", True, "active", "key_invalid"),
        (CANDIDATE, {"candidate_id": CANDIDATE, "x": 1}, "pending", True, "active", "data"),
        (CANDIDATE, {}, "pending", True, "active", "data"),
        (CANDIDATE, {"candidate_id": CANDIDATE}, "tracked", True, "active", "not_cardable"),
        (CANDIDATE, {"candidate_id": CANDIDATE}, "pending", False, "active", "not_cardable"),
        (CANDIDATE, {"candidate_id": CANDIDATE}, "pending", True, "quiet", "posture"),
    ],
    ids=["bad-key", "extra-data", "no-data", "terminal", "no-snapshot", "posture-not-active"],
)
async def test_prepare_refuses_what_it_cannot_card(
    key: str, data: dict[str, Any], state: str, has_snapshot: bool, posture: str, code: str
) -> None:
    pool = _Pool(_row(state, SNAPSHOT if has_snapshot else None), posture=posture)
    source = cards.build_source(pool, owner_recipient=_owner)

    with pytest.raises(cards.CardRefused, match=code):
        await source.prepare(key, data)


async def test_prepare_refuses_an_unknown_candidate() -> None:
    source = cards.build_source(_Pool(None), owner_recipient=_owner)
    with pytest.raises(cards.CardRefused, match="not_cardable"):
        await source.prepare(CANDIDATE, {"candidate_id": CANDIDATE})


@pytest.mark.parametrize("owner", ["other-chat", "none"], ids=["other-chat", "unavailable"])
async def test_prepare_refuses_when_the_owner_recipient_changed(owner: str) -> None:
    reader = _other_owner_chat if owner == "other-chat" else _no_owner
    source = cards.build_source(_Pool(_row()), owner_recipient=reader)
    with pytest.raises(cards.CardRefused, match="owner_recipient_changed"):
        await source.prepare(CANDIDATE, {"candidate_id": CANDIDATE})


async def test_a_carded_candidate_returns_its_card_whatever_the_posture_now() -> None:
    """The repeated step after a crash must find the existing card, not refuse it."""
    source = cards.build_source(_Pool(_row("carded"), posture="quiet"), owner_recipient=_no_owner)
    prompt, _ = await source.prepare(CANDIDATE, {"candidate_id": CANDIDATE})
    assert prompt.recipient.chat_id == "42"


@pytest.mark.parametrize(
    ("ref", "value", "message"),
    [(CANDIDATE, "resolve", "unregistered"), ("not-a-uuid", "track", "ref_invalid")],
    ids=["unregistered-value", "bad-ref"],
)
async def test_apply_refuses_before_any_read(ref: str, value: str, message: str) -> None:
    class _NoPool:
        def acquire(self) -> Any:
            raise AssertionError("read")

    with pytest.raises(ValueError, match=message):
        await cards.apply_disposition(_NoPool(), UUID(CANDIDATE), ref, value)
