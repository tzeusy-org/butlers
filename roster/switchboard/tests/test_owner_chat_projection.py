"""Owner-chat span qualification, row by row (REQ-butler-switchboard-011).

Pure tests of ``spans_of_row``; the paging, cursor and in-flight rules run against
migrated PostgreSQL in ``test_owner_chat_projection_db.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from butlers.roster_imports import roster_module

_projection = roster_module("switchboard", "tools/owner_chat/projection")
owner_chat_spans = _projection.owner_chat_spans
spans_of_row = _projection.spans_of_row

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
INBOX_ID = UUID("01936a00-0000-7000-8000-000000000001")
OWNER = "6590000001@s.whatsapp.net"
THEM = "6590000002@s.whatsapp.net"


def _entry(message_id: str, sender: str, body: str, **facts: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "message_id": message_id,
        "sender_identity": sender,
        "sender": "label",
        "text": body,
        "timestamp": (NOW - timedelta(hours=1)).isoformat(),
        "is_new": True,
        "reply_to": None,
    }
    entry.update(facts)
    return entry


def _row(
    history: list[dict[str, Any]],
    *,
    channel: str = "whatsapp_user_client",
    chat_type: str | None = "private",
    participant_count: int | None = 2,
    owner: str | None = OWNER,
    senders: list[str] | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "source_channel": channel,
        "source_endpoint_identity": "whatsapp:+6590000001",
        "external_conversation_id": "whatsapp:chat-1",
        "source_sender_identities": senders if senders is not None else [OWNER, THEM],
    }
    if chat_type is not None:
        context["chat_type"] = chat_type
    if participant_count is not None:
        context["participant_count"] = participant_count
    if owner is not None:
        context["owner_sender_identity"] = owner
    payload = {"payload": {"raw": {"conversation_history": history}}}
    return {"id": INBOX_ID, "request_context": json.dumps(context), "raw_payload": payload}


def _promise(**facts: Any) -> dict[str, Any]:
    return _entry("m-owner", OWNER, "I'll send it tomorrow", **{"from_history": False, **facts})


def _without(entry: dict[str, Any], key: str) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if k != key}


@pytest.mark.parametrize("from_history", [True, None, "false"], ids=["history", "null", "string"])
def test_a_whatsapp_entry_not_known_to_be_live_is_not_returned(from_history: Any) -> None:
    """History sync can carry text another linked device sent, a past bridge's included.

    Live delivery never echoes the bridge's own sends (whatsmeow skips its own device
    when it encrypts a send and dispatches no local event for it), so only an entry the
    bridge marked live, ``from_history`` false, is the owner's own typing.
    """
    entry = _promise(is_from_me=True, forwarded=False, from_history=from_history)
    assert spans_of_row(_row([entry]), NOW) == []


def test_a_whatsapp_entry_without_the_live_marker_is_not_returned() -> None:
    """An older bridge sends no marker: its entries are not returned, not guessed live."""
    entry = _without(_promise(is_from_me=True, forwarded=False), "from_history")
    assert spans_of_row(_row([entry]), NOW) == []


def _telegram_promise(**facts: Any) -> dict[str, Any]:
    return _entry(
        "4711",
        "telegram:100",
        "I'll send it tomorrow",
        **{"is_from_me": True, "forwarded": False, "via_bot": False, **facts},
    )


def _telegram_row(entry: dict[str, Any]) -> dict[str, Any]:
    return _row(
        [entry],
        channel="telegram_user_client",
        owner="telegram:100",
        senders=["telegram:100", "telegram:200"],
    )


def test_a_telegram_entry_typed_by_the_owner_is_returned() -> None:
    (span,) = spans_of_row(_telegram_row(_telegram_promise()), NOW)
    assert span["counterparty_identity"] == "telegram:200"


@pytest.mark.parametrize("via_bot", [True, None, "false"], ids=["via-bot", "null", "string"])
def test_a_telegram_entry_sent_via_a_bot_is_not_returned(via_bot: Any) -> None:
    """Inline-bot output is posted under the owner's account but written by the bot."""
    entry = _telegram_promise(via_bot=via_bot)
    assert spans_of_row(_telegram_row(entry), NOW) == []


def test_a_telegram_entry_without_the_via_bot_fact_is_not_returned() -> None:
    entry = _without(_telegram_promise(), "via_bot")
    assert spans_of_row(_telegram_row(entry), NOW) == []


def test_from_history_is_not_required_of_telegram_entries() -> None:
    entry = _telegram_promise()
    assert "from_history" not in entry
    assert len(spans_of_row(_telegram_row(entry), NOW)) == 1


def test_only_the_unforwarded_owner_entry_is_returned() -> None:
    row = _row(
        [
            _entry("m-them", THEM, "Can you send the deck?", is_from_me=False, forwarded=False),
            _promise(is_from_me=True, forwarded=False),
            _entry("m-fwd", OWNER, "I will pay you back", is_from_me=True, forwarded=True),
        ]
    )

    (span,) = spans_of_row(row, NOW)

    assert span == {
        "source_channel": "whatsapp_user_client",
        "endpoint_identity": "whatsapp:+6590000001",
        "chat_identity": "whatsapp:chat-1",
        "counterparty_identity": THEM,
        "provider_message_id": "m-owner",
        "event_time": (NOW - timedelta(hours=1)).isoformat(),
        "ingestion_event_id": str(INBOX_ID),
        "text": "I'll send it tomorrow",
    }


@pytest.mark.parametrize(
    "facts",
    [
        {"forwarded": False},
        {"is_from_me": True},
        {"is_from_me": "true", "forwarded": False},
        {"is_from_me": True, "forwarded": None},
        {"is_from_me": True, "forwarded": False, "is_new": False},
        {"is_from_me": True, "forwarded": False, "timestamp": None},
        {"is_from_me": True, "forwarded": False, "timestamp": "2026-11-14T11:00:00"},
        {"is_from_me": True, "forwarded": False, "timestamp": "2026-10-31T11:59:00+00:00"},
        {"is_from_me": True, "forwarded": False, "timestamp": "2026-11-14T12:00:01+00:00"},
        {"is_from_me": True, "forwarded": False, "text": ""},
    ],
    ids=[
        "missing-is_from_me",
        "missing-forwarded",
        "non-bool-is_from_me",
        "null-forwarded",
        "not-new",
        "no-event-time",
        "naive-event-time",
        "older-than-14-days",
        "future-event-time",
        "empty-text",
    ],
)
def test_an_entry_without_every_fact_is_not_returned(facts: dict[str, Any]) -> None:
    assert spans_of_row(_row([_promise(**facts)]), NOW) == []


def test_exactly_fourteen_days_old_still_qualifies() -> None:
    entry = _promise(is_from_me=True, forwarded=False, timestamp="2026-10-31T12:00:00+00:00")
    assert len(spans_of_row(_row([entry]), NOW)) == 1


@pytest.mark.parametrize(
    ("chat_type", "participant_count"),
    [("group", 2), ("private", 3), (None, 2), ("private", None)],
    ids=["group", "three-participants", "no-chat-type", "no-participant-count"],
)
def test_group_or_unknown_chats_are_excluded(
    chat_type: str | None, participant_count: int | None
) -> None:
    row = _row(
        [_promise(is_from_me=True, forwarded=False)],
        chat_type=chat_type,
        participant_count=participant_count,
    )
    assert spans_of_row(row, NOW) == []


def test_other_channels_are_excluded() -> None:
    row = _row([_promise(is_from_me=True, forwarded=False)], channel="telegram_bot")
    assert spans_of_row(row, NOW) == []


def test_is_from_me_with_a_different_sender_than_the_stored_owner_is_excluded() -> None:
    linked = "164772343488740@lid"
    entry = _entry("m-x", linked, "I'll send it", is_from_me=True, forwarded=False)
    row = _row([entry], senders=[OWNER, THEM, linked])
    assert spans_of_row(row, NOW) == []


def test_without_a_stored_owner_the_entry_facts_alone_qualify() -> None:
    row = _row([_promise(is_from_me=True, forwarded=False)], owner=None)
    (span,) = spans_of_row(row, NOW)
    assert span["counterparty_identity"] == THEM


@pytest.mark.parametrize(
    "senders",
    [[OWNER], [OWNER, THEM, "6590000003@s.whatsapp.net"]],
    ids=["owner-only-batch", "two-other-identities"],
)
def test_no_single_counterparty_means_no_span(senders: list[str]) -> None:
    row = _row([_promise(is_from_me=True, forwarded=False)], senders=senders)
    assert spans_of_row(row, NOW) == []


def test_metadata_only_rows_have_no_spans() -> None:
    row = _row([])
    row["raw_payload"] = {"payload": {"raw": None, "normalized_text": "[withheld]"}}
    assert spans_of_row(row, NOW) == []


async def test_unavailability_is_an_error_not_an_empty_page() -> None:
    class _Broken:
        async def fetch(self, *args: Any) -> list[Any]:
            raise ConnectionError("database unavailable")

    with pytest.raises(ConnectionError):
        await owner_chat_spans(_Broken(), after_received_at=None, after_inbox_id=None)


async def test_the_cursor_is_the_last_row_read_even_when_nothing_qualifies() -> None:
    received = NOW - timedelta(minutes=5)
    group = _row([_promise(is_from_me=True, forwarded=False)], chat_type="group")

    class _Pool:
        async def fetch(self, *args: Any) -> list[dict[str, Any]]:
            return [{**group, "received_at": received, "db_now": NOW}]

    page = await owner_chat_spans(_Pool(), after_received_at=None, after_inbox_id=None)

    assert page == {
        "spans": [],
        "cursor": {"received_at": received.isoformat(), "inbox_id": str(INBOX_ID)},
        "more": False,
    }


async def test_a_half_cursor_is_refused_before_any_read() -> None:
    class _NoPool:
        async def fetch(self, *args: Any) -> list[Any]:
            raise AssertionError("read attempted")

    with pytest.raises(ValueError, match="cursor_incomplete"):
        await owner_chat_spans(_NoPool(), after_received_at=NOW, after_inbox_id=None)
