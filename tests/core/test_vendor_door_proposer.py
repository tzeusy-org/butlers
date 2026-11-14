from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from butlers.core import vendor_door_proposer as proposer
from butlers.core import vendor_doors as flag

EVENT = uuid.uuid4()
TEXT = (
    "Manage your plan: https://Shop.Example.com/account/orders/ABC-123/cancel?token=zzz#x. "
    "Questions? https://shop.example.com/help"
)
TEMPLATE = "https://shop.example.com/account/orders/{order}/cancel"
SLOTS = {"order": "order_ref"}


def test_candidates_drop_query_fragment_and_trailing_punctuation():
    assert proposer.candidate_urls(TEXT) == [
        "https://shop.example.com/account/orders/ABC-123/cancel",
        "https://shop.example.com/help",
    ]


def test_candidates_are_bounded():
    text = " ".join(f"https://a.example.com/{i}" for i in range(500))
    assert len(proposer.candidate_urls(text)) == proposer.MAX_CANDIDATES


@pytest.mark.parametrize(
    ("template", "bound"),
    [
        (TEMPLATE, True),
        ("https://shop.example.com/help", True),
        ("https://shop.example.com/account/orders/{order}/refund", False),
        ("https://evil.example.net/account/orders/{order}/cancel", False),
        ("https://shop.example.com.evil.net/help", False),
        ("https://shop.example.com/account/{x}", False),
        ("https://shop.example.com/", False),
        ("https://shop.example.com/account", False),  # a prefix of a longer event URL
        ("https://{sub}.example.com/help", False),  # a slot in the host
        ("https://shop.{tld}/help", False),
        (
            "https://shop.example.com/account/orders/ord-{order}/cancel",
            False,
        ),  # not a whole segment
        ("https://shop.example.com/account/orders/{order}/cancel/", False),
        ("https://SHOP.example.com/help", True),  # host normalizes
        ("http://shop.example.com/help", False),
        ("https://shop.example.com", False),
        ("not a url", False),
    ],
)
def test_template_must_match_a_url_in_the_event(template, bound):
    assert proposer.template_in_event(template, TEXT) is bound


def test_userinfo_port_and_lookalike_urls_never_bind():
    text = "https://user@shop.example.com/help https://shop.example.com:8443/help"
    assert not proposer.template_in_event("https://shop.example.com/help", text)


@pytest.mark.parametrize("text", [None, ""])
def test_no_event_text_binds_nothing(text):
    assert not proposer.template_in_event(TEMPLATE, text)


class FakeConn:
    def __init__(self, text: str | None):
        self.text = text
        self.executed: list[str] = []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def transaction(self, **_):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, *args):
        self.executed.append(sql)

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        if "message_inbox" in sql:
            return self.text
        return {"outcome": "stored", "tier": "unverified"}


async def _propose(conn, **kw):
    return await proposer.propose_from_event(
        cast(Any, conn),
        event_id=kw.pop("event_id", EVENT),
        kind="cancel",
        template=kw.pop("template", TEMPLATE),
        slot_types=SLOTS,
        **kw,
    )


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(flag, "VENDOR_DOORS_LIVE", True)


async def test_dark_by_default_touches_nothing():
    conn = FakeConn(TEXT)
    assert await _propose(conn) == {"outcome": "refused", "reason": "dark"}
    assert conn.executed == [] and conn.calls == []


async def test_bound_template_is_proposed_under_the_switchboard_role(live):
    conn = FakeConn(TEXT)
    out = await _propose(conn)
    assert out["outcome"] == "stored"
    assert conn.executed == ["SET LOCAL ROLE butler_switchboard_rw"]
    sql, args = conn.calls[-1]
    assert "vendor_door_propose" in sql
    # Only the six function arguments: the real event id, nothing claiming origin or verdict.
    assert args == ("cancel", TEMPLATE, SLOTS, EVENT, None, {})


async def test_template_not_in_this_event_is_refused_without_a_write(live):
    conn = FakeConn("nothing to see at https://other.example.org/x")
    out = await _propose(conn)
    assert out == {"outcome": "refused", "reason": "not_in_event"}
    assert all("vendor_door_propose" not in sql for sql, _ in conn.calls)


async def test_unknown_event_is_refused(live):
    conn = FakeConn(None)
    assert (await _propose(conn))["reason"] == "event"
    assert all("vendor_door_propose" not in sql for sql, _ in conn.calls)


async def test_the_event_text_is_read_by_the_passed_event_id(live):
    conn = FakeConn(TEXT)
    other = uuid.uuid4()
    await _propose(conn, event_id=other)
    assert conn.calls[0][1] == (other,)
    assert conn.calls[-1][1][3] == other


async def test_slot_may_stand_for_exactly_one_segment():
    text = "https://shop.example.com/a/b/c/cancel"
    assert not proposer.template_in_event("https://shop.example.com/a/{x}/cancel", text)
    assert proposer.template_in_event("https://shop.example.com/a/{x}/c/cancel", text)


# --- the MCP tool body ---------------------------------------------------------------------


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, conn):
        self.conn = conn
        self.acquired = 0

    def acquire(self):
        self.acquired += 1
        return _Acquire(self.conn)


def _session(monkeypatch, request_id):
    monkeypatch.setattr(
        proposer,
        "get_current_runtime_session_routing_context",
        lambda: {"request_id": request_id} if request_id else None,
    )


async def _tool(pool, **kw):
    args = {
        "event_id": str(EVENT),
        "kind": "cancel",
        "url_template": TEMPLATE.replace("{order}", "{order_ref}"),
    }
    return await proposer.propose_for_session(cast(Any, pool), **{**args, **kw})


async def test_tool_is_disabled_without_touching_anything(monkeypatch):
    _session(monkeypatch, str(EVENT))
    pool = FakePool(FakeConn(TEXT))
    assert await _tool(pool) == {"status": "disabled"}
    assert pool.acquired == 0


async def test_tool_refuses_an_event_that_is_not_this_sessions_request(live, monkeypatch):
    pool = FakePool(FakeConn(TEXT))
    _session(monkeypatch, str(uuid.uuid4()))
    assert (await _tool(pool))["reason"] == "event_not_in_session"
    _session(monkeypatch, None)
    assert (await _tool(pool))["reason"] == "event_not_in_session"
    assert (await _tool(pool, event_id="not-a-uuid"))["reason"] == "event"
    assert pool.acquired == 0


async def test_tool_refuses_unknown_slot_names(live, monkeypatch):
    _session(monkeypatch, str(EVENT))
    pool = FakePool(FakeConn(TEXT))
    out = await _tool(pool, url_template="https://shop.example.com/a/{anything}/cancel")
    assert out == {"status": "refused", "reason": "slot_type"}
    assert pool.acquired == 0


async def test_tool_proposes_a_bound_template_for_its_own_request(live, monkeypatch):
    _session(monkeypatch, str(EVENT).upper())
    conn = FakeConn(TEXT)
    out = await _tool(FakePool(conn))
    assert out == {"status": "stored"}
    args = conn.calls[-1][1]
    assert args[3] == EVENT and args[2] == {"order_ref": "order_ref"}


async def test_tool_reports_an_unbound_template_as_refused(live, monkeypatch):
    _session(monkeypatch, str(EVENT))
    out = await _tool(FakePool(FakeConn("no links here")), url_template="https://a.example.com/x")
    assert out == {"status": "refused", "reason": "not_in_event"}


# --- the role stays inside the transaction -------------------------------------------------


class _TxConn(FakeConn):
    """Models SET LOCAL: the role holds only while the transaction is open."""

    def __init__(self, text):
        super().__init__(text)
        self.in_tx = False
        self.role = "none"

    def transaction(self, **_):
        return self

    async def __aenter__(self):
        self.in_tx = True
        return self

    async def __aexit__(self, *exc):
        self.in_tx = False
        self.role = "none"  # asyncpg ends the transaction; SET LOCAL is discarded
        return False

    async def execute(self, sql, *args):
        assert self.in_tx, "SET LOCAL outside a transaction would not hold"
        assert sql.startswith("SET LOCAL ROLE")
        self.role = sql.rsplit(" ", 1)[1]


async def test_role_is_set_locally_and_does_not_outlive_the_call(live):
    conn = _TxConn(TEXT)
    await _propose(conn)
    assert conn.role == "none" and not conn.in_tx


async def test_role_does_not_leak_when_the_proposal_is_refused(live):
    conn = _TxConn("nothing")
    await _propose(conn)
    assert conn.role == "none" and not conn.in_tx
