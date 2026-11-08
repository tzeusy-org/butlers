"""Single-use approved-notify proof (RFC 0038 D13, bu-dhloi1.168).

The owning source mints a proof only while its executor holds the live,
approved, unexecuted ``notify`` row. The proof binds the action, the stored-call
digest and the exact canonical envelope, expires within seconds and is consumed
by its first verification. It is a separate operation from the RFC 0023
``approval_request`` admission and never widens that mint.
"""

from __future__ import annotations

import asyncio
import copy
import socket
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp import FastMCP

from butlers.core import approval_delivery_authority as authority
from butlers.core.approval_delivery_authority import (
    ApprovalAuthorityTopology,
    SourceApprovalAdmission,
    SourceApprovedNotifyDispatch,
    verify_switchboard_approved_notify,
)
from butlers.core.approval_delivery_transport import RecoveryAuthorityError
from butlers.core.approved_delivery import (
    ApprovedNotifyAttestation,
    approved_delivery_covering,
    approved_notify_request,
    bind_verified_approved_delivery,
    notify_request_digest,
    reset_verified_approved_delivery,
)
from butlers.modules.approvals.execution_context import approval_tool_args_digest

pytestmark = pytest.mark.unit

_STORED: dict[str, Any] = {"intent": "send", "channel": "telegram", "message": "Approved words"}


class _Rows:
    """The source repository's live-row read: approved, unexecuted notify only."""

    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, dict[str, Any]] = {}
        self.reads: list[tuple[uuid.UUID, str]] = []

    async def approved_notify_args(
        self, action_id: uuid.UUID, *, owning_schema: str, args_digest: str
    ) -> dict[str, Any] | None:
        self.reads.append((action_id, owning_schema))
        row = self.rows.get(action_id)
        if row is None or approval_tool_args_digest(row) != args_digest:
            return None
        return copy.deepcopy(row)


def _source() -> tuple[SourceApprovalAdmission, _Rows, uuid.UUID, str, dict[str, Any]]:
    rows = _Rows()
    action_id = uuid.uuid4()
    rows.rows[action_id] = copy.deepcopy(_STORED)
    source = SourceApprovalAdmission(rows, owning_schema="relationship")
    return (
        source,
        rows,
        action_id,
        approval_tool_args_digest(_STORED),
        approved_notify_request("relationship", _STORED),
    )


async def test_proof_verifies_once_for_the_exact_live_envelope() -> None:
    source, rows, action_id, digest, payload = _source()
    proof = await source.mint_approved_notify(action_id, digest, payload)

    first = await source.verify_approved_notify(copy.deepcopy(payload), proof)
    replay = await source.verify_approved_notify(copy.deepcopy(payload), proof)

    assert first == {"allowed": True, "source_epoch": source.epoch, "action_id": str(action_id)}
    assert replay == {"allowed": False}
    assert all(schema == "relationship" for _, schema in rows.reads)


async def test_changed_envelope_is_refused_and_consumes_the_proof() -> None:
    source, _, action_id, digest, payload = _source()
    proof = await source.mint_approved_notify(action_id, digest, payload)
    changed = copy.deepcopy(payload)
    changed["delivery"]["message"] = "Different words"

    assert await source.verify_approved_notify(changed, proof) == {"allowed": False}
    # The first presentation consumed it, whatever the outcome.
    assert await source.verify_approved_notify(payload, proof) == {"allowed": False}


async def test_expired_proof_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    source, _, action_id, digest, payload = _source()
    monkeypatch.setattr(authority, "_APPROVED_NOTIFY_PROOF_SECONDS", 0.0)
    proof = await source.mint_approved_notify(action_id, digest, payload)

    assert await source.verify_approved_notify(payload, proof) == {"allowed": False}


@pytest.mark.parametrize("change", ["executed", "edited"])
async def test_row_executed_or_changed_after_mint_is_refused(change: str) -> None:
    source, rows, action_id, digest, payload = _source()
    proof = await source.mint_approved_notify(action_id, digest, payload)
    if change == "executed":
        # The repository read returns nothing once the row is executed,
        # rejected, expired or no longer approved.
        rows.rows.pop(action_id)
    else:
        rows.rows[action_id]["message"] = "Edited after approval"

    assert await source.verify_approved_notify(payload, proof) == {"allowed": False}


async def test_unknown_proof_and_retired_source_are_refused() -> None:
    source, _, action_id, digest, payload = _source()
    assert await source.verify_approved_notify(payload, "never-minted") == {"allowed": False}
    assert await source.verify_approved_notify(payload, None) == {"allowed": False}

    proof = await source.mint_approved_notify(action_id, digest, payload)
    source.retire()
    assert await source.verify_approved_notify(payload, proof) == {"allowed": False}
    with pytest.raises(RecoveryAuthorityError):
        await source.mint_approved_notify(action_id, digest, payload)


def _mint_refusals() -> list[Any]:
    other_action = uuid.uuid4()
    return [
        pytest.param(lambda a, d, p: (other_action, d, p), id="action-not-approved-or-executed"),
        pytest.param(lambda a, d, p: (a, "0" * 64, p), id="stored-call-digest-mismatch"),
        pytest.param(
            lambda a, d, p: (a, d, {**p, "delivery": {**p["delivery"], "message": "X"}}),
            id="envelope-not-the-stored-call",
        ),
        pytest.param(lambda a, d, p: (a, d, {**p, "origin_butler": "health"}), id="foreign-origin"),
        pytest.param(
            lambda a, d, p: (
                a,
                d,
                {**p, "delivery": {**p["delivery"], "intent": "approval_request"}},
            ),
            id="owner-control-intent",
        ),
    ]


@pytest.mark.parametrize("mutate", _mint_refusals())
async def test_mint_refuses_anything_but_the_live_stored_call(mutate) -> None:
    source, _, action_id, digest, payload = _source()

    with pytest.raises(RecoveryAuthorityError):
        await source.mint_approved_notify(*mutate(action_id, digest, payload))
    assert source._approved_notifications == {}


async def test_mint_does_not_widen_the_approval_request_admission() -> None:
    """An approved-notify proof is useless to the RFC 0023 recovery verifier."""
    source, _, action_id, digest, payload = _source()
    proof = await source.mint_approved_notify(action_id, digest, payload)

    assert source._admissions == {}
    assert (await source.verify(payload, proof)).get("allowed") is not True
    # And the separate store still holds it (verify() did not consume it).
    assert (await source.verify_approved_notify(payload, proof))["allowed"] is True


async def test_dispatch_revokes_an_unpresented_proof() -> None:
    source, _, action_id, digest, payload = _source()
    presented: list[str] = []

    class _Endpoint:
        async def call(self, tool: str, arguments: dict[str, Any], *, proof: str | None = None):
            assert tool == "deliver"
            assert arguments == {"source_butler": "relationship", "notify_request": payload}
            assert proof is not None
            presented.append(proof)
            raise RecoveryAuthorityError("transport failed before verification")

    dispatch = SourceApprovedNotifyDispatch(source, _Endpoint())  # type: ignore[arg-type]
    with pytest.raises(RecoveryAuthorityError):
        await dispatch(action_id, digest, payload)

    assert len(presented) == 1
    assert await source.verify_approved_notify(payload, presented[0]) == {"allowed": False}


async def test_unauthenticated_in_process_call_gets_no_attestation() -> None:
    """Outside the kernel-admitted Switchboard ingress there is no principal."""
    module = SimpleNamespace(_approval_recovery_verifiers={})
    _, _, _, _, payload = _source()

    assert await verify_switchboard_approved_notify(module, payload) is None
    assert authority.approved_notify_proof_presented() is False


def _route_payload(issuer: str, notify_request: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "route.v1",
        "request_context": {
            "request_id": "019c2710-0000-7000-8000-000000000001",
            "received_at": "2026-11-08T00:00:00+00:00",
            "source_channel": "mcp",
            "source_endpoint_identity": "switchboard",
            "source_sender_identity": issuer,
        },
        "input": {"prompt": "deliver", "context": {"notify_request": notify_request}},
    }


async def test_attestation_covers_only_its_exact_route_in_its_own_task() -> None:
    _, _, action_id, _, payload = _source()
    attestation = ApprovedNotifyAttestation(
        issuer="relationship", action_id=action_id, notify_digest=notify_request_digest(payload)
    )
    route = _route_payload("relationship", payload)
    assert approved_delivery_covering("route.execute", route) is None  # bare: nothing bound

    token = bind_verified_approved_delivery(attestation)
    try:
        assert approved_delivery_covering("route.execute", route) == attestation
        assert approved_delivery_covering("telegram_send_message", route) is None
        changed = copy.deepcopy(route)
        changed["input"]["context"]["notify_request"]["delivery"]["message"] = "X"
        assert approved_delivery_covering("route.execute", changed) is None
        foreign = _route_payload("health", payload)
        assert approved_delivery_covering("route.execute", foreign) is None
        recovery = copy.deepcopy(route)
        recovery["input"]["context"]["notify_request"]["recovery"] = {"operation": "handoff"}
        assert approved_delivery_covering("route.execute", recovery) is None
        # A child task inherits the contextvar but not the authority.
        assert await asyncio.create_task(_covering_in_child(route)) is None
    finally:
        reset_verified_approved_delivery(token)
    assert approved_delivery_covering("route.execute", route) is None


async def _covering_in_child(route: dict[str, Any]) -> Any:
    return approved_delivery_covering("route.execute", route)


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="kernel peer admission")
async def test_kernel_socket_chain_verifies_once_and_refuses_replay_and_foreign(
    tmp_path,
) -> None:
    """Source executor -> Switchboard -> source verifier, over the real sockets.

    The Switchboard companion sees the source principal and has the source
    verify (and consume) the proof. A replayed proof, a proof for another
    envelope and a call with no proof all fail to produce an attestation.
    """
    source, _, action_id, digest, payload = _source()
    seen: list[Any] = []
    switchboard = FastMCP("approved-notify-switchboard")
    messenger = FastMCP("approved-notify-messenger")
    module = SimpleNamespace()

    @switchboard.tool(name="deliver")
    async def deliver(source_butler: str, notify_request: dict) -> dict:
        presented = authority.approved_notify_proof_presented()
        attestation = await verify_switchboard_approved_notify(module, notify_request)
        # A second verification of the same proof inside the same call is a replay.
        replay = await verify_switchboard_approved_notify(module, notify_request)
        seen.append((source_butler, presented, attestation, replay))
        return {"attested": attestation is not None}

    @messenger.tool(name="route.execute")
    async def route_execute() -> dict:
        return {}

    topology = ApprovalAuthorityTopology(
        sources={"relationship": source},
        switchboard_registered=switchboard,
        switchboard_module=module,
        messenger_registered=messenger,
        messenger_registry_url="http://localhost:41102/mcp",
        socket_directory=tmp_path,
    )
    async with asyncio.timeout(10), topology:
        hop = topology.approved_notify_dispatch("relationship")
        assert await hop(action_id, digest, payload) == {"attested": True}
        endpoint = topology._dispatches["relationship"]._endpoint
        # No proof, and a proof that was never minted: no attestation.
        assert await endpoint.call(
            "deliver", {"source_butler": "relationship", "notify_request": payload}
        ) == {"attested": False}
        assert await endpoint.call(
            "deliver",
            {"source_butler": "relationship", "notify_request": payload},
            proof="forged-proof",
        ) == {"attested": False}
        # A live proof minted for this envelope cannot cover a different one.
        proof = await source.mint_approved_notify(action_id, digest, payload)
        changed = copy.deepcopy(payload)
        changed["delivery"]["message"] = "Different words"
        assert await endpoint.call(
            "deliver", {"source_butler": "relationship", "notify_request": changed}, proof=proof
        ) == {"attested": False}
        assert await source.verify_approved_notify(payload, proof) == {"allowed": False}
        with pytest.raises(RecoveryAuthorityError):
            topology.approved_notify_dispatch("health")

    source_butler, presented, attestation, replay = seen[0]
    assert (source_butler, presented) == ("relationship", True)
    assert attestation == ApprovedNotifyAttestation(
        issuer="relationship", action_id=action_id, notify_digest=notify_request_digest(payload)
    )
    assert replay is None
    assert [entry[1] for entry in seen[1:]] == [False, True, True]
    assert all(entry[2] is None for entry in seen[1:])
    assert list(tmp_path.iterdir()) == []
