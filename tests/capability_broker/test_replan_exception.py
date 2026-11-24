"""The calendar replan exception, broker half (RFC 0038 D13, bu-qlx055 q12 = B).

The broker judges the call (an unexposed goal moving one event in time) and
forwards it conditionally; the calendar domain judges the event. These are the
pure and transport rows; the ledger rows run on a database in
``test_broker_ledger.py``.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastmcp import FastMCP

from butlers.capability_broker import assertions, policy
from butlers.capability_broker.assertions import (
    LIFETIME_SECONDS,
    REPLAN_MOVE,
    AssertionSigner,
    verify_assertion,
)
from butlers.capability_broker.contracts import Decision, Disposition, Reservation
from butlers.capability_broker.forwarder import HttpForwarder
from butlers.capability_broker.policy import REPLAN, RULE_ELIGIBLE, replan
from butlers.capability_broker.verification import (
    AssertionVerificationMiddleware,
    DomainVerifier,
)
from butlers.core import goal_lineage
from butlers.core.capability_json import arguments_digest
from butlers.core.runtime_probe_control.keys import VerifierKey, VerifierKeyring
from butlers.mcp_wrappers import _SpanWrappingMCP
from butlers.modules.calendar import replan as calendar_replan
from tests.capability_broker.helpers import capability, principal

pytestmark = pytest.mark.unit

MOVE = {"event_id": "evt-1", "start_at": "2026-02-20T16:00:00+00:00"}
UPDATE = capability("calendar_update_event", "communicate", "general")


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(policy, "REPLAN_MOVES_LIVE", True)


@pytest.fixture(autouse=True)
def _reset_enforcement():
    yield
    goal_lineage.set_enforcement(False)


# -- the call -------------------------------------------------------------------


def test_dark_every_move_parks_as_before():
    assert policy.REPLAN_MOVES_LIVE is False
    assert not replan(principal(), UPDATE, MOVE)


def test_live_an_unexposed_pure_move_is_offered_conditionally(live):
    assert replan(principal(), UPDATE, MOVE)
    assert replan(principal(), UPDATE, {**MOVE, "end_at": "x", "calendar_id": "c"})
    assert REPLAN == Decision(Disposition.PARK, allow_standing_rule=True, conditional=REPLAN_MOVE)
    # A decline parks exactly as the rule-eligible park it replaced.
    assert (REPLAN.disposition, REPLAN.allow_standing_rule) == (
        RULE_ELIGIBLE.disposition,
        RULE_ELIGIBLE.allow_standing_rule,
    )


@pytest.mark.parametrize(
    ("who", "cap", "args"),
    [
        ("exposed", UPDATE, MOVE),
        ("owner", capability("calendar_delete_event", "communicate", "general"), MOVE),
        ("owner", UPDATE, {**MOVE, "title": "Renamed"}),
        ("owner", UPDATE, {**MOVE, "attendees": []}),
        ("owner", UPDATE, {**MOVE, "request_id": "r"}),
        ("owner", UPDATE, {**MOVE, "recurrence_scope": "this"}),
        ("owner", UPDATE, {"event_id": "evt-1"}),
        ("owner", UPDATE, {"start_at": "x"}),
    ],
)
def test_live_anything_else_parks(live, who, cap, args):
    assert not replan(principal(exposed=who == "exposed"), cap, args)


def test_the_broker_and_the_domain_agree_on_a_move():
    assert policy.REPLAN_MOVE_ARGS == calendar_replan.MOVE_ARGS
    assert policy.REPLAN_CAPABILITY == calendar_replan.REPLAN_TOOL
    assert REPLAN_MOVE == calendar_replan.REPLAN_MOVE
    assert assertions.DECLINED_STATUS[REPLAN_MOVE] == calendar_replan.REPLAN_DECLINED


# -- the assertion ----------------------------------------------------------------

NOW = 1_800_000_000


def _payload(**overrides) -> dict[str, Any]:
    payload = {
        "aud": "general",
        "principal": "goal",
        "goal_id": str(uuid4()),
        "invocation_id": str(uuid4()),
        "depth": 0,
        "origin": "owner",
        "exposed": False,
        "capability": "calendar_update_event",
        "args_digest": arguments_digest(MOVE),
        "call_id": str(uuid4()),
        "effect_id": str(uuid4()),
        "approval_ref": None,
        "exp": NOW + 60,
    }
    payload.update(overrides)
    return payload


def _verify(signer, assertion):
    return verify_assertion(
        assertion,
        keys={signer.kid: signer.ed25519.public_key()},
        audience="general",
        capability="calendar_update_event",
        arguments=MOVE,
        now=NOW,
        claim_call_id=lambda call_id, exp: True,
    )


def test_a_conditional_assertion_verifies_and_binds_its_condition():
    signer = AssertionSigner("k1", Ed25519PrivateKey.generate())
    payload = _verify(signer, signer.sign(_payload(conditional=REPLAN_MOVE)))
    lineage = goal_lineage.VerifiedLineage.from_payload(payload)
    assert lineage.conditional == REPLAN_MOVE
    plain = goal_lineage.VerifiedLineage.from_payload(_verify(signer, signer.sign(_payload())))
    assert plain.conditional is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"conditional": "anything"},
        {"conditional": None},
        # An approved call is the owner's act, never conditional.
        {"conditional": REPLAN_MOVE, "approval_ref": "a1"},
        {"conditional": REPLAN_MOVE, "effect_id": None},
    ],
)
def test_any_other_condition_is_refused(overrides):
    signer = AssertionSigner("k1", Ed25519PrivateKey.generate())
    with pytest.raises(ValueError, match="assertion_"):
        signer.sign(_payload(**overrides))


def test_a_dashboard_lineage_is_never_conditional():
    with pytest.raises(ValueError):
        goal_lineage.VerifiedLineage(
            goal_id=None,
            invocation_id=None,
            depth=0,
            origin="owner",
            exposed=False,
            capability="calendar_update_event",
            args_digest="0" * 64,
            call_id=uuid4(),
            principal="dashboard",
            conditional=REPLAN_MOVE,
        )


# -- the transport ----------------------------------------------------------------


def _reservation(decision: Decision) -> Reservation:
    return Reservation(principal(), UPDATE, decision, uuid4(), uuid4(), arguments_digest(MOVE))


def _assertion(signer, reserved: Reservation, conditional: str | None) -> str:
    p = reserved.principal
    extra = {} if conditional is None else {"conditional": conditional}
    return signer.sign(
        {
            **extra,
            "aud": "general",
            "principal": "goal",
            "goal_id": str(p.goal_id),
            "invocation_id": str(p.invocation_id),
            "depth": p.depth,
            "origin": p.origin,
            "exposed": p.exposed,
            "capability": "calendar_update_event",
            "args_digest": reserved.args_digest,
            "call_id": str(reserved.call_id),
            "effect_id": str(reserved.effect_id),
            "approval_ref": None,
            "exp": int(time.time()) + LIFETIME_SECONDS,
        }
    )


@asynccontextmanager
async def _domain(signer, seen: list):
    server = FastMCP("general")
    proxy = _SpanWrappingMCP(server, "general", module_name="calendar")

    @proxy.tool()
    async def calendar_update_event(event_id: str, start_at: str) -> dict[str, Any]:
        condition = goal_lineage.conditional_for("calendar_update_event")
        seen.append(condition)
        if event_id == "boom":
            # A write error after the send, as the calendar tool re-raises it (bu-4c7nvp.1.13).
            raise RuntimeError("provider write failed")
        if condition is not None:
            return calendar_replan.declined(
                "not_butler_created", event_id=event_id, calendar_id="primary"
            )
        return {"status": calendar_replan.REPLAN_DECLINED}  # plain text from an update

    keyring = VerifierKeyring(
        VerifierKey(
            signer.kid,
            signer.ed25519.public_key().public_bytes_raw(),
            datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    verifier = DomainVerifier("general", keyring, started_at=int(time.time()) - 5)
    server.middleware.insert(0, AssertionVerificationMiddleware(verifier))
    goal_lineage.set_enforcement(True)
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)

    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), **kwargs)

    with patch("butlers.mcp_wrappers.capture_tool_call"):
        async with app.lifespan(app):
            yield HttpForwarder({"general": "http://domain/mcp"}, httpx_client_factory=factory)


async def test_a_domain_decline_of_a_conditional_call_is_a_declined_receipt():
    signer = AssertionSigner("k1", Ed25519PrivateKey.generate())
    seen: list = []
    reserved = _reservation(REPLAN)
    async with _domain(signer, seen) as forwarder:
        receipt = await forwarder.dispatch(
            reserved, MOVE, _assertion(signer, reserved, REPLAN_MOVE), idempotency_key=None
        )
    assert seen == [REPLAN_MOVE]
    assert receipt.status == "declined"
    assert receipt.value is None


async def test_the_same_text_from_an_unconditional_call_is_just_a_result():
    signer = AssertionSigner("k1", Ed25519PrivateKey.generate())
    seen: list = []
    reserved = _reservation(Decision(Disposition.DISPATCH))
    async with _domain(signer, seen) as forwarder:
        receipt = await forwarder.dispatch(
            reserved, MOVE, _assertion(signer, reserved, None), idempotency_key=None
        )
    assert seen == [None]
    assert receipt.status == "committed"


async def test_a_write_error_under_the_condition_is_a_failed_receipt_never_committed():
    signer = AssertionSigner("k1", Ed25519PrivateKey.generate())
    seen: list = []
    boom = {**MOVE, "event_id": "boom"}
    reserved = Reservation(principal(), UPDATE, REPLAN, uuid4(), uuid4(), arguments_digest(boom))
    async with _domain(signer, seen) as forwarder:
        receipt = await forwarder.dispatch(
            reserved, boom, _assertion(signer, reserved, REPLAN_MOVE), idempotency_key=None
        )
    assert seen == [REPLAN_MOVE]
    assert receipt.status == "failed"
