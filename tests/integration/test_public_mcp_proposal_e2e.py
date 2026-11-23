# ruff: noqa: F811 -- the imported pytest fixtures are redefined by name as test parameters
"""A public MCP proposal, end to end, on the real two-database topology.

REQ-public-mcp-proposals-006 (the proposal's state and decision time are readable through
``fetch("proposal:<id>")`` as ``parked``, ``applied``, ``owner_rejected`` and ``expired``) and
-008 (a proposal changes no fact until the owner approves its parked action).

The pieces run in the order production runs them. The one seam not exercised is the daemon's
hop from the executor to the gated tool: ``_dispatch`` (reused from the Relationship tests) builds
an ``ApprovalsModule`` whose tool executor calls the registered ``relationship_assert_fact`` handler
directly, then runs ``_dispatch_approved_action_by_id`` on the approved action, instead of going
through a running daemon's MCP session. The approve, the dispatch, the writer and the rest are real:

1. ``propose_fact`` through the real ``CapabilityHost.handle`` with a signed assertion writes
   ``mcp_inbox.proposals`` as ``mcp_public_ro``;
2. one importer pass, reading the inbox as ``mcp_proposals_reader`` over TCP, parks one
   ``relationship_assert_fact`` action per proposal on the primary;
3. the owner approves one through the shared approvals operation, bound to the call the typed
   dashboard review displays (REQ-dashboard-approvals-904), and the shared executor
   dispatches the registered ``relationship_assert_fact``; the owner rejects another; a third
   is left until its approval window has passed;
4. a second importer pass reconciles the three states; logical replication carries them to the
   projection and ``fetch`` reads them.

Every instant handed to the importer is fixed; the approval window is crossed by moving the
importer's ``now``, never by sleeping. The live dev run of the same paths is
``scripts/k8s/public_mcp_proposal_e2e.py`` and is pending the public MCP server deploy.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from butlers.modules.approvals import operations
from butlers.roster_imports import roster_module
from roster.relationship.tests.test_external_fact_proposals import _approve, _dispatch
from tests.integration.test_public_mcp_proposal_import import (  # noqa: F401 -- fixtures
    World,
    admin,
    inbox,
    primary,
    projected,
)
from tests.integration.test_public_mcp_proposals import CID, SUB, client  # noqa: F401

importer = roster_module("relationship", "tools/public_mcp_proposal_import")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

#: Importer instants are offsets from the inbox's own ``received_at`` (the database's clock,
#: which the public role cannot set), so the proposals are past the importer's settle window.
FIRST_PASS = timedelta(hours=1)
#: The owner decides inside the window; the third proposal is never decided.
SECOND_PASS = timedelta(hours=2)
#: Past the 14-day approval window, so the undecided action expires on this pass.
RECONCILE_PASS = timedelta(days=15)


async def _propose(client, subject, obj, key) -> str:
    result = await client.call(
        "propose_fact",
        {
            "subject_entity_id": str(subject),
            "predicate": "works-at",
            "object_entity_id": str(obj),
            "idempotency_key": key,
        },
    )
    assert result["isError"] is False, result
    assert result["structuredContent"]["status"] == "received"
    return result["structuredContent"]["proposal_id"]


async def _fetch_state(client, proposal_id: str, state: str, *, seconds: float = 40) -> dict:
    """``fetch`` until logical replication has carried ``state`` to the projection."""
    deadline = time.monotonic() + seconds
    while True:
        result = await client.call("fetch", {"id": f"proposal:{proposal_id}"})
        if not result["isError"] and result["structuredContent"]["metadata"]["state"] == state:
            return result["structuredContent"]
        assert time.monotonic() < deadline, f"{proposal_id} never reached {state}: {result}"
        await asyncio.sleep(0.1)


async def _facts_from(admin, proposal_id: str) -> list:
    return await admin.fetch(
        "SELECT * FROM relationship.entity_facts WHERE src = $1", f"external_mcp:{proposal_id}"
    )


async def test_proposals_reach_applied_rejected_and_expired(
    client, projected, primary, admin, inbox
) -> None:
    world = World(projected, admin)
    people = [await world.person() for _ in range(7)]
    # Three distinct pairs; none is the owner, which the importer would refuse as a subject.
    pairs = {
        "applied": (people[0], people[1]),
        "rejected": (people[2], people[3]),
        "expired": (people[4], people[5]),
    }

    # 1. Propose through the real host, as the public server's caller does.
    ids = {name: await _propose(client, a, b, f"e2e-{name}") for name, (a, b) in pairs.items()}
    assert len(set(ids.values())) == 3
    uuids = {name: uuid.UUID(value) for name, value in ids.items()}
    assert world.in_inbox(uuids.values()) == 3

    # 2. One importer pass: copied, deleted from the inbox, parked with one action each.
    with projected[1].connect() as conn:
        received = conn.execute(
            text("SELECT max(received_at) FROM mcp_inbox.proposals")
        ).scalar_one()
    # Offsets run from the exact receipt instant. Flooring it (to the hour, say) can leave
    # ``base + FIRST_PASS`` inside the importer's settle window when a proposal lands just
    # before the boundary, and the first pass would then leave every proposal ``imported``.
    base = received.astimezone(UTC)
    assert base + FIRST_PASS - importer.SETTLE >= received
    report = await importer.run_public_mcp_proposal_import(
        primary, now=base + FIRST_PASS, inbox=inbox
    )
    assert report["intake"] == "copied" and report["errors"] == {}
    assert world.in_inbox(uuids.values()) == 0
    assert await world.states(uuids.values()) == dict.fromkeys(uuids.values(), "parked")
    actions = {}
    for name, pid in uuids.items():
        (action,) = await world.actions(pid)
        assert action["status"] == "pending"
        assert action["tool_name"] == "relationship_assert_fact"
        actions[name] = action["id"]

    # REQ-public-mcp-proposals-008: parked, so no fact exists yet, and fetch reports exactly that.
    for value in ids.values():
        assert await _facts_from(admin, value) == []
        parked = await _fetch_state(client, value, "parked")
        assert parked["metadata"]["decided_at"] is None

    # 3. The owner decides. Approve through the shared operation and let the shared executor
    #    dispatch relationship_assert_fact; reject another; leave the third undecided.
    approved = await _approve(primary, actions["applied"])
    assert "error" not in approved and approved["status"] == "approved"
    # Approval alone writes no fact; the dispatch does.
    assert await _facts_from(admin, ids["applied"]) == []
    executed = await _dispatch(primary, actions["applied"])
    assert "error" not in executed and executed["status"] == "executed"

    rejected = await operations.reject_action(primary, str(actions["rejected"]), "not him")
    assert "error" not in rejected and rejected["status"] == "rejected"

    # The importer has not run since: the proposals still read parked, and an intermediate
    # pass inside the window changes neither the expiry nor the undecided proposal.
    await importer.run_public_mcp_proposal_import(primary, now=base + SECOND_PASS, inbox=inbox)
    assert (await world.actions(uuids["expired"]))[0]["status"] == "pending"

    # 4. Reconcile past the approval window, then read each state through fetch.
    report = await importer.run_public_mcp_proposal_import(
        primary, now=base + RECONCILE_PASS, inbox=inbox
    )
    assert report["errors"] == {}
    assert report["expired_actions"] == 1

    applied = await _fetch_state(client, ids["applied"], "applied")
    rejected_view = await _fetch_state(client, ids["rejected"], "owner_rejected")
    expired = await _fetch_state(client, ids["expired"], "expired")
    for view, pid in ((applied, ids["applied"]), (rejected_view, ids["rejected"])):
        assert view["id"] == f"proposal:{pid}" and view["metadata"]["decided_at"] is not None
    assert datetime.fromisoformat(expired["metadata"]["decided_at"]) == base + RECONCILE_PASS
    # fetch returns the state and its time, never the payload, note or submitter.
    for view in (applied, rejected_view, expired):
        assert set(view["metadata"]) >= {"state", "decided_at", "domain", "kind"}
        assert SUB not in view["text"] and CID not in str(view)

    # REQ-public-mcp-proposals-008, after the decisions: only the approved proposal has a
    # fact, third-party and attributed to the outside source; rejected and expired have none.
    (fact,) = await _facts_from(admin, ids["applied"])
    assert fact["content_authority"] == "third_party"
    assert fact["src"] == f"external_mcp:{ids['applied']}"
    assert (fact["subject"], fact["predicate"], fact["object"]) == (
        pairs["applied"][0],
        "works-at",
        str(pairs["applied"][1]),
    )
    assert fact["assert_origin"] == "approved" and fact["assert_action_id"] == actions["applied"]
    for name in ("rejected", "expired"):
        assert await _facts_from(admin, ids[name]) == []
    assert await world.states(uuids.values()) == {
        uuids["applied"]: "applied",
        uuids["rejected"]: "owner_rejected",
        uuids["expired"]: "expired",
    }
