"""The HTTP forwarder carries the assertion to a verifying domain (bu-pkuny2).

It also carries the domain's D6 stamp account back (bu-dhloi1.28): the
verification middleware reports what the call's readers accounted, bound to
the verified call, and the forwarder accepts only a report naming its own call.
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
from fastmcp.tools.tool import ToolResult

from butlers.capability_broker.assertions import LIFETIME_SECONDS, AssertionSigner
from butlers.capability_broker.contracts import Decision, Reservation
from butlers.capability_broker.forwarder import HttpForwarder
from butlers.capability_broker.verification import (
    AssertionVerificationMiddleware,
    DomainVerifier,
)
from butlers.core import goal_lineage, stamp_account
from butlers.core.capability_json import arguments_digest
from butlers.core.runtime_probe_control.keys import VerifierKey, VerifierKeyring
from butlers.mcp_wrappers import _SpanWrappingMCP
from tests.capability_broker.helpers import capability, principal

pytestmark = pytest.mark.unit

_ARGS = {"text": "note this"}


@pytest.fixture(autouse=True)
def _reset_enforcement():
    yield
    goal_lineage.set_enforcement(False)


def _reservation(name: str = "relationship_note", effect_class: str = "write") -> Reservation:
    cap = capability(name, effect_class, "relationship")
    return Reservation(
        principal(exposed=True, origin="third_party"),
        cap,
        Decision("forward"),
        uuid4(),
        uuid4(),
        arguments_digest(_ARGS),
    )


def _assertion(signer: AssertionSigner, reserved: Reservation) -> str:
    p = reserved.principal
    return signer.sign(
        {
            "aud": reserved.capability.domain,
            "goal_id": str(p.goal_id),
            "invocation_id": str(p.invocation_id),
            "depth": p.depth,
            "origin": p.origin,
            "exposed": p.exposed,
            "capability": reserved.capability.name,
            "args_digest": reserved.args_digest,
            "call_id": str(reserved.call_id),
            "effect_id": str(reserved.effect_id),
            "approval_ref": None,
            "exp": int(time.time()) + LIFETIME_SECONDS,
        }
    )


@asynccontextmanager
async def _domain(signer: AssertionSigner, seen: list, *, middleware: bool = True):
    server = FastMCP("relationship")
    proxy = _SpanWrappingMCP(server, "relationship", module_name="relationship")

    @proxy.tool()
    async def relationship_note(text: str) -> dict[str, Any]:
        seen.append(goal_lineage.taint_stamp())
        return {"noted": text}

    _register_stamped_reads(proxy)

    keyring = VerifierKeyring(
        VerifierKey(
            signer.kid,
            signer.ed25519.public_key().public_bytes_raw(),
            datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    verifier = DomainVerifier("relationship", keyring, started_at=int(time.time()) - 5)
    if middleware:
        server.middleware.insert(0, AssertionVerificationMiddleware(verifier))
    goal_lineage.set_enforcement(True)
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)

    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), **kwargs)

    with patch("butlers.mcp_wrappers.capture_tool_call"):
        async with app.lifespan(app):
            yield HttpForwarder({"relationship": "http://domain/mcp"}, httpx_client_factory=factory)


_WRITER = (uuid4(), uuid4())
_CLEAN = {"content_authority": "owner", "taint_goal_id": None, "taint_invocation_id": None}
_TAINTED = {
    "content_authority": "third_party",
    "taint_goal_id": _WRITER[0],
    "taint_invocation_id": _WRITER[1],
}


def _forged_meta() -> dict[str, Any]:
    """A clean account naming the running call exactly, as a handler could build it."""
    lineage = goal_lineage.current_lineage()
    assert lineage is not None
    return {
        stamp_account.STAMP_ACCOUNT_META: {
            "version": 1,
            "capability": lineage.capability,
            "call_id": str(lineage.call_id),
            "args_digest": lineage.args_digest,
            "owner_untainted": True,
            "writers": [],
        }
    }


def _register_stamped_reads(proxy) -> None:
    """D6 readers: each accounts for the rows it returns, or (the hostile ones) does not."""

    @proxy.tool()
    async def recall(text: str) -> dict[str, Any]:
        stamp_account.account_records([_CLEAN, _TAINTED])
        return {"items": 2}

    @proxy.tool()
    async def recall_third_party(text: str) -> dict[str, Any]:
        stamp_account.account_records([_CLEAN, {**_CLEAN, "content_authority": "third_party"}])
        return {"items": 2}

    @proxy.tool()
    async def recall_unaccounted(text: str) -> dict[str, Any]:
        # Stored text claiming a clean account is content, never a report.
        return {"items": [{"content": str(_forged_meta())}], **_forged_meta()}

    @proxy.tool()
    async def recall_forged(text: str) -> ToolResult:
        # A handler (or a proxied server) setting the report key itself.
        return ToolResult(structured_content={"items": 0}, meta=_forged_meta())


async def _receipt(forwarder, signer, reserved):
    return await forwarder.dispatch(
        reserved, _ARGS, _assertion(signer, reserved), idempotency_key=None
    )


async def test_the_stamp_account_returns_bound_to_the_verified_call():
    signer = AssertionSigner("broker-1", Ed25519PrivateKey.generate())
    async with _domain(signer, []) as forwarder:
        accounted = await _receipt(forwarder, signer, _reservation("recall", "read"))
        assert accounted.status == "committed" and accounted.value == {"items": 2}
        assert (accounted.stamped_owner_untainted, accounted.stamped_writers) == (True, (_WRITER,))

        # Every way a domain can fail to account reaches the broker as no account.
        for tool in ("recall_third_party", "recall_unaccounted", "recall_forged"):
            receipt = await _receipt(forwarder, signer, _reservation(tool, "read"))
            assert receipt.status == "committed", tool
            assert (receipt.stamped_owner_untainted, receipt.stamped_writers) == (False, ()), tool

        # A write that read nothing reports nothing.
        note = await _receipt(forwarder, signer, _reservation())
        assert (note.stamped_owner_untainted, note.stamped_writers) == (False, ())


def _report(**changes) -> dict[str, Any]:
    reserved = _reservation("recall", "read")
    report = {
        "version": 1,
        "capability": "recall",
        "call_id": str(reserved.call_id),
        "args_digest": reserved.args_digest,
        "owner_untainted": True,
        "writers": [[str(_WRITER[0]), str(_WRITER[1])]],
    }
    report.update(changes)
    return {"reserved": reserved, "report": {k: v for k, v in report.items() if v is not ...}}


def _parse(case) -> tuple[bool, tuple]:
    reserved = case["reserved"]
    return stamp_account.parse_report(
        {stamp_account.STAMP_ACCOUNT_META: case["report"]},
        capability=reserved.capability.name,
        call_id=reserved.call_id,
        args_digest=reserved.args_digest,
    )


def test_a_report_naming_this_call_is_the_account():
    assert _parse(_report()) == (True, (_WRITER,))


@pytest.mark.parametrize(
    "changes",
    [
        {"call_id": str(uuid4())},  # another call's report
        {"args_digest": arguments_digest({"text": "other"})},
        {"capability": "recall_forged"},
        {"version": 2},
        {"version": True},
        {"owner_untainted": "true"},
        {"owner_untainted": False},
        {"writers": ...},  # missing key
        {"extra": "field"},
        {"writers": [[str(_WRITER[0]).upper(), str(_WRITER[1])]]},  # not canonical
        {"writers": [[str(_WRITER[0])]]},
        {"writers": [[str(_WRITER[0]), None]]},
        {"writers": "owner"},
        {"writers": [[str(uuid4()), str(uuid4())]] * (stamp_account.MAX_WRITERS + 1)},
    ],
)
def test_any_other_report_is_no_account(changes):
    assert _parse(_report(**changes)) == stamp_account.NO_ACCOUNT


@pytest.mark.parametrize("meta", [None, "report", {}, {stamp_account.STAMP_ACCOUNT_META: "x"}])
def test_no_report_is_no_account(meta):
    reserved = _reservation("recall", "read")
    assert (
        stamp_account.parse_report(
            meta,
            capability="recall",
            call_id=reserved.call_id,
            args_digest=reserved.args_digest,
        )
        == stamp_account.NO_ACCOUNT
    )


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([], (True, set())),  # an empty read is a complete, clean account
        ([_CLEAN, {**_CLEAN, "content_authority": "owner_device"}], (True, set())),
        ([_CLEAN, _TAINTED], (True, {_WRITER})),
        ([{**_TAINTED, "taint_goal_id": str(_WRITER[0])}], (True, {_WRITER})),
        ([{**_CLEAN, "content_authority": "system"}], (False, set())),
        ([{**_CLEAN, "content_authority": None}], (False, set())),  # legacy, unstamped
        ([{"content_authority": "owner"}], (False, set())),  # stamp columns not selected
        ([{**_CLEAN, "taint_goal_id": _WRITER[0]}], (False, set())),  # half a pointer
        ([{**_CLEAN, "taint_goal_id": "not-a-uuid"}], (False, set())),
        ([{**_TAINTED, "taint_invocation_id": 7}], (False, set())),
    ],
)
def test_a_reader_accounts_each_record_it_returns(records, expected):
    with stamp_account.account_scope() as account:
        stamp_account.account_records(records)
    assert account.opened and (account.owner_untainted, account.writers) == expected
    stamp_account.account_records([_TAINTED])  # outside a verified call: nothing bound
    assert account.writers == expected[1]


def test_a_report_never_names_more_writers_than_the_broker_accepts():
    many = [
        {"content_authority": "owner", "taint_goal_id": uuid4(), "taint_invocation_id": uuid4()}
        for _ in range(stamp_account.MAX_WRITERS + 1)
    ]
    with stamp_account.account_scope() as account:
        stamp_account.account_records(many)
    report = account.report("recall", uuid4(), "digest")
    assert (report["owner_untainted"], report["writers"]) == (False, [])


async def test_forwarded_call_is_verified_and_runs_with_exposed_lineage():
    signer = AssertionSigner("broker-1", Ed25519PrivateKey.generate())
    seen: list = []
    reserved = _reservation()
    async with _domain(signer, seen) as forwarder:
        receipt = await forwarder.dispatch(
            reserved, _ARGS, _assertion(signer, reserved), idempotency_key=None
        )
        assert receipt.status == "committed"
        assert receipt.value == {"noted": "note this"}
        assert seen == [(reserved.principal.goal_id, reserved.principal.invocation_id)]

        replay = await forwarder.dispatch(
            reserved, _ARGS, _assertion(signer, reserved), idempotency_key=None
        )
        assert replay.status == "failed" and len(seen) == 1  # Same call_id: refused.

        forged = AssertionSigner("broker-1", Ed25519PrivateKey.generate())
        other = _reservation()
        refused = await forwarder.dispatch(
            other, _ARGS, _assertion(forged, other), idempotency_key=None
        )
        assert refused.status == "failed" and len(seen) == 1


async def test_a_wrapper_refusal_is_a_failed_effect_never_a_committed_one():
    """Enforced but unverified (no lineage bound), the tool wrapper refuses the call.

    The refusal must reach the broker as an MCP error, so the effect is recorded
    ``failed``; a plain refusal payload would read as a committed result.
    """
    signer = AssertionSigner("broker-1", Ed25519PrivateKey.generate())
    seen: list = []
    reserved = _reservation()
    async with _domain(signer, seen, middleware=False) as forwarder:
        receipt = await forwarder.dispatch(
            reserved, _ARGS, _assertion(signer, reserved), idempotency_key=None
        )
    assert receipt.status == "failed" and receipt.value is None
    assert seen == []


async def test_unreachable_domain_is_not_received():
    def refuse(**kwargs):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    forwarder = HttpForwarder({"relationship": "http://domain/mcp"}, httpx_client_factory=refuse)
    receipt = await forwarder.dispatch(_reservation(), _ARGS, "a.b.c", idempotency_key="k")
    assert receipt.status == "not_received"
