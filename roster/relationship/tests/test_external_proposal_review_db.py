"""Typed approvals review of an external proposal on PostgreSQL (REQ-dashboard-approvals-904).

The proposal is parked by the real Relationship helper and recorded in the real
``relationship.external_fact_proposals``; the dashboard routes read it, entity state comes
from ``public.entities`` and the owner's decision runs the shared approve and deny
operations. Only the hop to the owning daemon is a recorded collaborator: dispatch is
proven by ``test_external_fact_proposals.py`` and ``tests/integration``. The owner context
is the synthetic ``owner_device`` fixture, not authentication evidence.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from butlers.api import external_proposal_review as review_mod
from butlers.api.deps import get_db
from butlers.api.routers import approvals as routes
from butlers.modules.approvals.external_proposals import EXTERNAL_PROPOSAL_RULE_REFUSAL
from butlers.roster_imports import roster_module
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

from .fact_authority_fixtures import synthetic_owner

external = roster_module("relationship", "tools/external_fact_proposals")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_RECEIVED_AT = datetime(2026, 11, 1, 8, 30, tzinfo=UTC)
_URL = "https://example.com/synthetic/evidence?ref=1"
_NOTE = "Synthetic note naming https://example.com/elsewhere."


@pytest.fixture
async def rel_pool(db):
    return await db("core", "memory", "relationship", "approvals")


async def _entity(pool, name: str, entity_type: str = "person", roles=None) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ($1, $2, $3) RETURNING id",
        f"{name} {uuid.uuid4().hex[:6]}",
        entity_type,
        roles or [],
    )


@pytest.fixture
async def parked(rel_pool):
    await _entity(rel_pool, "Synthetic owner", roles=["owner"])
    subject = await _entity(rel_pool, "Synthetic Person")
    obj = await _entity(rel_pool, "Synthetic Org", "organization")
    proposal_id = uuid.uuid4()
    async with rel_pool.acquire() as conn:
        admission = await external.park_external_fact_proposal(
            conn,
            proposal_id=proposal_id,
            subject=subject,
            predicate="works-at",
            object_entity_id=obj,
            client_label="synthetic-client",
            evidence_url=_URL,
            received_at=_RECEIVED_AT,
        )
    await rel_pool.execute(
        """
        INSERT INTO relationship.external_fact_proposals
            (proposal_id, state, note, evidence_url, sub, cid, idempotency_key,
             subject_entity_id, predicate, object_entity_id, received_at, imported_at,
             pending_action_id)
        VALUES ($1, 'parked', $2, $3, 'synthetic-sub', 'https://client.example/cimd', 'k1',
                $4, 'works-at', $5, $6, $6, $7)
        """,
        proposal_id,
        _NOTE,
        _URL,
        subject,
        obj,
        _RECEIVED_AT,
        admission.action_id,
    )
    return SimpleNamespace(
        action_id=admission.action_id, proposal_id=proposal_id, subject=subject, object=obj
    )


@pytest.fixture
async def client(rel_pool, monkeypatch):
    dispatched: list[tuple[str, dict[str, Any]]] = []

    async def find(*_args):
        return "relationship", rel_pool

    async def named(*_args):
        return [("relationship", rel_pool)]

    async def no_card(*_args):
        return None

    async def dispatch(_mcp, _db, _pool, action_id, tool_name, tool_args, _butler):
        dispatched.append((tool_name, tool_args))
        return routes._DispatchOutcome(kind="unreachable")

    monkeypatch.setattr(routes, "_find_action_pool", find)
    monkeypatch.setattr(routes, "_find_named_approvals_pools", named)
    monkeypatch.setattr(routes, "_dashboard_owner_delivery_card", no_card)
    monkeypatch.setattr(routes, "_decision_memory_writer_for", lambda *a: None)
    monkeypatch.setattr(routes, "_dispatch_approved_action_outcome", dispatch)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(
        butler_names=["relationship"], pool=lambda _name: rel_pool
    )
    app.dependency_overrides[routes.get_mcp_manager] = lambda: object()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        http.dispatched = dispatched  # type: ignore[attr-defined]
        yield http


async def _review(client, action_id) -> tuple[dict, dict]:
    response = await client.get(f"/api/approvals/{action_id}")
    assert response.status_code == 200, response.text
    detail = response.json()["data"]
    return detail, detail["external_proposal_review"]


async def _status(pool, action_id) -> str:
    return await pool.fetchval("SELECT status FROM pending_actions WHERE id=$1", action_id)


# REQ-dashboard-approvals-904: "The owner sees who and what before approving" and
# "Evidence links are inert" (the note and URL come back verbatim, as data).
async def test_review_reads_entities_and_proposal_record(client, parked) -> None:
    detail, review = await _review(client, parked.action_id)
    assert review["proposal_id"] == str(parked.proposal_id)
    assert review["client_label"] == "synthetic-client"
    assert review["predicate"] == "works-at"
    assert review["subject"]["id"] == str(parked.subject)
    assert review["subject"]["name"].startswith("Synthetic Person")
    assert review["subject"]["entity_type"] == "person"
    assert review["object"]["entity_type"] == "organization"
    assert review["subject"]["status"] == review["object"]["status"] == "live"
    assert review["note"] == _NOTE and review["evidence_url"] == _URL
    assert review["unresolvable_entity"] is False
    assert review["displayed_call"]["tool_name"] == "relationship_assert_fact"
    assert detail["call_preview"] is None
    assert "tool_args" not in detail["proposed_action"]
    # Outside text stays out of pending_actions (RFC 0039 D13 step 3).
    assert _URL not in json.dumps(detail["evidence"]) and _NOTE not in json.dumps(detail["why"])


# REQ-dashboard-approvals-904: "Approval submits the displayed call".
async def test_approve_with_displayed_call_decides_and_dispatches_stored_call(
    client, parked, rel_pool
) -> None:
    _detail, review = await _review(client, parked.action_id)
    stored = await rel_pool.fetchval(
        "SELECT tool_args FROM pending_actions WHERE id=$1", parked.action_id
    )
    async with synthetic_owner(rel_pool):
        response = await client.post(
            f"/api/approvals/actions/{parked.action_id}/approve",
            json={"displayed_call": review["displayed_call"]},
        )
    assert response.status_code == 200, response.text
    assert await _status(rel_pool, parked.action_id) == "approved"
    assert client.dispatched == [("relationship_assert_fact", stored)]


async def test_changed_call_is_refused_without_a_decision(client, parked, rel_pool) -> None:
    _detail, review = await _review(client, parked.action_id)
    await rel_pool.execute(
        'UPDATE pending_actions SET tool_args = tool_args || \'{"predicate": "knows"}\' '
        "WHERE id=$1",
        parked.action_id,
    )
    async with synthetic_owner(rel_pool):
        response = await client.post(
            f"/api/approvals/{parked.action_id}/approve",
            json={"displayed_call": review["displayed_call"]},
        )
    assert response.status_code == 409
    assert await _status(rel_pool, parked.action_id) == "pending"
    assert client.dispatched == []


# REQ-dashboard-approvals-904: "No rule can be created from the review".
async def test_create_rule_is_refused(client, parked, rel_pool) -> None:
    _detail, review = await _review(client, parked.action_id)
    async with synthetic_owner(rel_pool):
        response = await client.post(
            f"/api/approvals/actions/{parked.action_id}/approve",
            json={"displayed_call": review["displayed_call"], "create_rule": True},
        )
    assert response.status_code == 409
    assert response.json()["detail"] == EXTERNAL_PROPOSAL_RULE_REFUSAL
    assert await _status(rel_pool, parked.action_id) == "pending"
    assert await rel_pool.fetchval("SELECT count(*) FROM approval_rules") == 0


# REQ-dashboard-approvals-904: "Unresolvable entity blocks approval but not rejection".
@pytest.mark.parametrize("tombstone", ["merged_into", "deleted_at", "tombstone"])
async def test_tombstoned_subject_blocks_approve_and_reject_still_resolves(
    client, parked, rel_pool, tombstone
) -> None:
    _detail, before = await _review(client, parked.action_id)
    shown = before["displayed_call"]
    value = {
        "merged_into": str(parked.object),
        "deleted_at": "2026-11-02T00:00:00Z",
        "tombstone": "true",
    }[tombstone]
    await rel_pool.execute(
        "UPDATE public.entities SET metadata = COALESCE(metadata, '{}'::jsonb) "
        "|| jsonb_build_object($2::text, $3::text) WHERE id=$1",
        parked.subject,
        tombstone,
        value,
    )
    _detail, review = await _review(client, parked.action_id)
    status = "merged" if tombstone == "merged_into" else "deleted"
    assert review["unresolvable_entity"] is True
    assert review["subject"]["status"] == status
    assert review["displayed_call"] is None
    assert "subject" in review["approve_unavailable_reason"]

    async with synthetic_owner(rel_pool):
        refused = await client.post(
            f"/api/approvals/{parked.action_id}/approve", json={"displayed_call": shown}
        )
    assert refused.status_code == 409
    assert refused.json()["detail"] == review["approve_unavailable_reason"]
    assert await _status(rel_pool, parked.action_id) == "pending"
    assert client.dispatched == []

    async with synthetic_owner(rel_pool):
        rejected = await client.post(
            f"/api/approvals/{parked.action_id}/deny", json={"reason": "entity merged"}
        )
    assert rejected.status_code == 200, rejected.text
    assert await _status(rel_pool, parked.action_id) == "rejected"


# REQ-dashboard-approvals-904: only the action the importer parked for a proposal earns a
# displayed call. A proposal record naming a different action leaves this one unreviewable.
async def test_proposal_naming_another_action_offers_no_displayed_call(
    client, parked, rel_pool
) -> None:
    _detail, before = await _review(client, parked.action_id)
    shown = before["displayed_call"]
    other_proposal = uuid.uuid4()
    async with rel_pool.acquire() as conn:
        other = await external.park_external_fact_proposal(
            conn,
            proposal_id=other_proposal,
            subject=parked.subject,
            predicate="works-at",
            object_entity_id=parked.object,
            client_label="synthetic-client",
            evidence_url=None,
            received_at=_RECEIVED_AT,
        )
    await rel_pool.execute(
        "UPDATE relationship.external_fact_proposals SET pending_action_id=$2 WHERE proposal_id=$1",
        parked.proposal_id,
        other.action_id,
    )

    _detail, review = await _review(client, parked.action_id)
    assert review["displayed_call"] is None
    assert review["approve_unavailable_reason"] == review_mod._RECORD_UNAVAILABLE
    assert review["note"] is None and review["evidence_url"] is None

    async with synthetic_owner(rel_pool):
        refused = await client.post(
            f"/api/approvals/{parked.action_id}/approve", json={"displayed_call": shown}
        )
    assert refused.status_code == 409
    assert await _status(rel_pool, parked.action_id) == "pending"
    assert client.dispatched == []
