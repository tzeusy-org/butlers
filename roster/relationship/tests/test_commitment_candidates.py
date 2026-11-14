"""Owner-chat commitment candidate reducer, without a database (bu-jv1moa.11).

REQ-commitment-lifecycle-011 and -012. The store's constraints, replay,
concurrency, lapse and purge run on migrated PostgreSQL in
``test_commitment_candidates_db.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from butlers.identity import ResolvedContact
from butlers.roster_imports import roster_module

cc = roster_module("relationship", "tools/commitment_candidates")

pytestmark = pytest.mark.unit

ZONE = ZoneInfo("Asia/Singapore")
#: Monday 2026-11-09 21:00 in Singapore.
EVENT = datetime(2026, 11, 9, 13, 0, tzinfo=UTC)
NOW = EVENT + timedelta(days=2)
SAM = UUID("01936a00-0000-7000-8000-0000000000a1")
BODY = "I'll send you the deck tomorrow"


def _span(**overrides: Any) -> dict[str, Any]:
    span: dict[str, Any] = {
        "source_channel": "whatsapp_user_client",
        "endpoint_identity": "6590000001@s.whatsapp.net",
        "chat_identity": "6590000002@s.whatsapp.net",
        "counterparty_identity": "6590000002@s.whatsapp.net",
        "provider_message_id": "3EB0ABC",
        "event_time": EVENT.isoformat(),
        "ingestion_event_id": "01936a00-0000-7000-8000-000000000001",
        "text": BODY,
    }
    span.update(overrides)
    return {k: v for k, v in span.items() if v is not None}


class _Pool:
    def __init__(self, *, seen: bool = False, inserted: bool = True) -> None:
        self.seen = seen
        self.inserted = inserted
        self.inserts: list[tuple[Any, ...]] = []
        self.sql: list[str] = []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.sql.append(sql)
        if "SELECT 1 FROM relationship.commitment_candidates" in sql:
            return 1 if self.seen else None
        if "INSERT INTO relationship.commitment_candidates" in sql:
            self.inserts.append(args)
            return 1 if self.inserted else None
        if sql.strip() == "SELECT now()":
            return NOW
        raise AssertionError(sql)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "contact": ResolvedContact(name="Sam", roles=[], entity_id=SAM, is_unidentified=False),
        "active": None,
        "resolved": [],
    }

    async def resolve(pool: Any, channel: str, value: str, *, raise_on_error: bool = False):
        assert raise_on_error is True
        state["resolved"].append((channel, value))
        return state["contact"]

    async def active(pool: Any, *, source: str, fingerprint: str):
        state["fingerprint"] = (source, fingerprint)
        return state["active"]

    monkeypatch.setattr(cc, "resolve_contact_by_channel", resolve)
    monkeypatch.setattr(cc, "get_active_condition", active)
    return state


async def _produce(pool: _Pool, span: dict[str, Any]) -> str:
    return await cc.produce_candidate(pool, span, owner_zone=ZONE, now=NOW)


async def test_a_verified_owner_promise_creates_one_attributed_candidate(world) -> None:
    pool = _Pool()

    assert await _produce(pool, _span()) == "created"

    (args,) = pool.inserts
    key, kind, direction, entity, action, deadline, confidence, marker = args[:8]
    fingerprint, channel, evidence, taint, event_time = args[8:]
    assert key == cc.source_key(
        channel="whatsapp_user_client",
        endpoint="6590000001@s.whatsapp.net",
        chat="6590000002@s.whatsapp.net",
        provider_message_id="3EB0ABC",
        direction="owner_to_other",
    )
    assert (kind, direction, entity, channel, taint) == (
        "promise",
        "owner_to_other",
        SAM,
        "whatsapp_user_client",
        "untrusted_origin",
    )
    assert action == "send you the deck"
    assert 0 < confidence <= 1 and marker
    assert world["fingerprint"] == (
        "relationship:commitment",
        cc.commitment_fingerprint(
            source="relationship:commitment",
            counterparty_entity_id=str(SAM),
            action_description="send you the deck",
        ),
    )
    assert fingerprint == world["fingerprint"][1]
    assert evidence == (
        '{"ingestion_event_id": "01936a00-0000-7000-8000-000000000001", '
        '"provider_message_id": "3EB0ABC"}'
    )
    assert event_time == EVENT
    # Deadline anchors to the message (Monday in Singapore), not to processing time.
    assert deadline.astimezone(ZONE).date() == datetime(2026, 11, 10).date()
    # The message text is never written.
    assert BODY not in repr(args)
    assert world["resolved"] == [("whatsapp_user_client", "6590000002@s.whatsapp.net")]


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_channel": "telegram_bot"},
        {"endpoint_identity": None},
        {"chat_identity": None},
        {"counterparty_identity": None},
        {"provider_message_id": None},
        {"ingestion_event_id": None},
        {"text": None},
        {"text": ""},
        {"event_time": "2026-11-09T13:00:00"},
        {"event_time": "not a time"},
    ],
    ids=[
        "other-channel",
        "no-endpoint",
        "no-chat",
        "no-counterparty",
        "no-message-id",
        "no-ingestion-id",
        "no-text",
        "empty-text",
        "naive-time",
        "bad-time",
    ],
)
async def test_a_malformed_span_reads_nothing(world, overrides: dict[str, Any]) -> None:
    pool = _Pool()

    assert await _produce(pool, _span(**overrides)) == "malformed"
    assert pool.sql == [] and world["resolved"] == []


@pytest.mark.parametrize(
    "event_time",
    ["2026-11-11T13:00:01+00:00", "2026-10-28T13:00:00+00:00"],
    ids=["future", "exactly-14-days"],
)
async def test_a_span_outside_the_horizon_is_never_proposed(world, event_time: str) -> None:
    pool = _Pool()

    assert await _produce(pool, _span(event_time=event_time)) == "outside_horizon"
    assert pool.sql == []


async def test_a_message_already_seen_is_not_re_evaluated(world) -> None:
    pool = _Pool(seen=True)

    assert await _produce(pool, _span()) == "seen"
    assert world["resolved"] == [] and pool.inserts == []


@pytest.mark.parametrize(
    "contact",
    [
        None,
        ResolvedContact(name=None, roles=[], entity_id=None, is_unidentified=False),
        ResolvedContact(name="?", roles=[], entity_id=SAM, is_unidentified=True),
        ResolvedContact(name="Me", roles=["owner"], entity_id=SAM, is_unidentified=False),
    ],
    ids=["unresolved", "no-entity", "unidentified", "owner"],
)
async def test_an_unknown_or_owner_counterparty_creates_nothing(world, contact) -> None:
    world["contact"] = contact
    pool = _Pool()

    assert await _produce(pool, _span()) == "counterparty_unknown"
    assert pool.inserts == []


@pytest.mark.parametrize(
    "text",
    [
        "I should probably send you the deck",
        "I'll send it if I find it",
        "Will you send me the deck?",
        "Lunch was great",
    ],
    ids=["hedged", "conditional", "question", "no-marker"],
)
async def test_no_commitment_creates_nothing(world, text: str) -> None:
    pool = _Pool()

    assert await _produce(pool, _span(text=text)) == "no_commitment"
    assert pool.inserts == []


async def test_an_over_long_action_is_refused_not_truncated(world) -> None:
    pool = _Pool()
    text = "I'll send you " + "the very long report " * 6

    assert await _produce(pool, _span(text=text)) == "action_too_long"
    assert pool.inserts == []


async def test_an_action_of_121_characters_is_refused(world) -> None:
    pool = _Pool()

    assert await _produce(pool, _span(text="I'll send you " + "x" * 112)) == "action_too_long"
    assert pool.inserts == []


@pytest.mark.parametrize(
    ("text", "outcome"),
    [
        # Marker "I told Maximilian Wellington I'll": 33 characters.
        ("I told Maximilian Wellington I'll send the deck", "marker_too_long"),
        # Marker "I told Maximilian Wellingtn I'll": exactly 32.
        ("I told Maximilian Wellingtn I'll send the deck", "created"),
    ],
    ids=["33", "32"],
)
async def test_a_marker_over_32_characters_is_refused_not_stored(
    world, text: str, outcome: str
) -> None:
    """The relay form's marker carries a name; it is never stored past the CHECK bound."""
    pool = _Pool()

    assert await _produce(pool, _span(text=text)) == outcome
    assert len(pool.inserts) == (outcome == "created")


async def test_an_action_of_exactly_120_characters_is_proposed(world) -> None:
    pool = _Pool()
    action = "send you " + "x" * 111

    assert await _produce(pool, _span(text=f"I'll {action}")) == "created"
    assert pool.inserts[0][4] == action


async def test_an_already_tracked_promise_is_not_proposed_again(world) -> None:
    world["active"] = {"id": "condition"}
    pool = _Pool()

    assert await _produce(pool, _span()) == "already_tracked"
    assert pool.inserts == []


async def test_a_unique_conflict_reports_a_duplicate(world) -> None:
    pool = _Pool(inserted=False)

    assert await _produce(pool, _span()) == "duplicate"


def test_the_source_key_changes_with_every_field_and_is_hex() -> None:
    base = dict(channel="c", endpoint="e", chat="h", provider_message_id="m", direction="d")
    key = cc.source_key(**base)
    assert len(key) == 64 and int(key, 16) >= 0
    for field in base:
        assert cc.source_key(**{**base, field: base[field] + "x"}) != key


@pytest.fixture
def relay(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"result": {"spans": [], "cursor": None, "more": False}}

    async def call_relay(name: str, args: dict, *, daemon: str, switchboard_url: str) -> Any:
        calls.update(name=name, args=args, daemon=daemon, url=switchboard_url)
        if isinstance(calls["result"], Exception):
            raise calls["result"]
        return calls["result"]

    monkeypatch.setattr("butlers.core.service_plane.client.call_relay", call_relay)
    return calls


async def test_the_reader_asks_switchboard_as_relationship_with_only_the_cursor(relay) -> None:
    read = cc.relay_span_reader(lambda: "http://switchboard:41100/sse")

    assert await read({"received_at": "2026-11-14T00:00:00+00:00", "inbox_id": "i"}) == {
        "spans": [],
        "cursor": None,
        "more": False,
    }
    assert relay["name"] == "owner_chat.spans" and relay["daemon"] == "relationship"
    assert relay["args"] == {
        "after_received_at": "2026-11-14T00:00:00+00:00",
        "after_inbox_id": "i",
    }
    await read(None)
    assert relay["args"] == {}


@pytest.mark.parametrize(
    ("url", "result"),
    [
        (None, {"spans": []}),
        ("http://sb/sse", "relay"),
        ("http://sb/sse", "unavailable"),
        ("http://sb/sse", "no-token"),
        ("http://sb/sse", {"cursor": None}),
    ],
    ids=["no-url", "refused", "unavailable", "no-token", "malformed"],
)
async def test_any_reader_failure_is_spans_unavailable(relay, url: Any, result: Any) -> None:
    from butlers.core.service_plane.client import RelayUnavailable
    from butlers.core.service_plane.relays import RelayRefused

    relay["result"] = (
        {
            "relay": RelayRefused("caller_not_allowed"),
            "unavailable": RelayUnavailable("http_404"),
            "no-token": FileNotFoundError("token"),
        }.get(result, result)
        if isinstance(result, str)
        else result
    )
    read = cc.relay_span_reader(lambda: url)

    with pytest.raises(cc.SpansUnavailable):
        await read(None)


class _Scan:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, cursor: Any = None) -> None:
        self.state: dict[str, Any] = {} if cursor is None else {cc.CURSOR_KEY: cursor}
        self.writes: list[Any] = []
        self.produced: list[str] = []

        async def state_get(pool: Any, key: str) -> Any:
            return self.state.get(key)

        async def state_set(pool: Any, key: str, value: Any) -> int:
            self.writes.append(value)
            self.state[key] = value
            return len(self.writes)

        async def lapse(pool: Any) -> dict[str, int]:
            return {"lapsed": 0, "purged": 0}

        async def zone(pool: Any) -> str:
            return "Asia/Singapore"

        async def produce(pool: Any, span: Any, *, owner_zone: Any, now: Any) -> str:
            if span.get("explode"):
                raise RuntimeError("database down")
            self.produced.append(span["provider_message_id"])
            return "created"

        monkeypatch.setattr(cc, "state_get", state_get)
        monkeypatch.setattr(cc, "state_set", state_set)
        monkeypatch.setattr(cc, "lapse_and_purge", lapse)
        monkeypatch.setattr(cc, "resolve_general_timezone", zone)
        monkeypatch.setattr(cc, "produce_candidate", produce)


def _page(ids: list[str], cursor: str | None, more: bool) -> dict[str, Any]:
    return {
        "spans": [{"provider_message_id": i} for i in ids],
        "cursor": None if cursor is None else {"received_at": cursor, "inbox_id": cursor},
        "more": more,
    }


async def test_while_not_live_no_span_is_read_and_the_cursor_stays(monkeypatch) -> None:
    run = _Scan(monkeypatch)

    async def read(cursor: Any) -> Any:
        raise AssertionError("read while dark")

    result = await cc.scan(_Pool(), read_spans=read, live=False)

    assert result["status"] == "skipped" and result["reason"] == "composition_absent"
    assert run.writes == []


async def test_pages_are_read_after_the_stored_cursor_until_no_more(monkeypatch) -> None:
    run = _Scan(monkeypatch, cursor={"received_at": "c0", "inbox_id": "c0"})
    pages = [_page(["a", "b"], "c1", True), _page(["c"], "c2", False)]
    reads: list[Any] = []

    async def read(cursor: Any) -> Any:
        reads.append(cursor)
        return pages[len(reads) - 1]

    result = await cc.scan(_Pool(), read_spans=read, live=True)

    assert result["status"] == "ok" and result["outcomes"] == {"created": 3}
    assert reads == [
        {"received_at": "c0", "inbox_id": "c0"},
        {"received_at": "c1", "inbox_id": "c1"},
    ]
    assert run.produced == ["a", "b", "c"]
    assert run.writes == [
        {"received_at": "c1", "inbox_id": "c1"},
        {"received_at": "c2", "inbox_id": "c2"},
    ]


async def test_an_unavailable_projection_leaves_the_cursor(monkeypatch) -> None:
    run = _Scan(monkeypatch, cursor={"received_at": "c0", "inbox_id": "c0"})

    async def read(cursor: Any) -> Any:
        raise cc.SpansUnavailable("relay")

    result = await cc.scan(_Pool(), read_spans=read, live=True)

    assert result["status"] == "unavailable" and run.writes == []


async def test_a_failure_inside_a_page_does_not_move_the_cursor(monkeypatch) -> None:
    run = _Scan(monkeypatch)
    page = _page(["a"], "c1", False)
    page["spans"].append({"provider_message_id": "b", "explode": True})

    async def read(cursor: Any) -> Any:
        return page

    with pytest.raises(RuntimeError):
        await cc.scan(_Pool(), read_spans=read, live=True)
    assert run.writes == []
