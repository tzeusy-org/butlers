"""Typed dashboard review of an external MCP fact proposal (REQ-dashboard-approvals-904).

Software boundary: the real routes, DTOs, reader and displayed-call check over a
stateful fake connection. The approve operation, audit and dispatch are recorded
collaborators. PostgreSQL behaviour (the real tables, entity tombstones, the
owner's decision and the shared deny path) is proven in
``roster/relationship/tests/test_external_proposal_review_db.py``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from butlers.api import external_proposal_review as review_mod
from butlers.api.deps import get_db
from butlers.api.models.approval import DisplayedApprovalCall
from butlers.api.routers import approvals as routes
from butlers.modules.approvals.external_proposals import (
    EXTERNAL_PROPOSAL_RULE_REFUSAL,
    ExternalReviewBinding,
)
from butlers.modules.approvals.previews import args_digest

pytestmark = pytest.mark.unit

_TEMPORAL_KEYS = (
    "effective_period_id",
    "effective_from",
    "effective_from_precision",
    "effective_to",
    "effective_to_precision",
    "corrects_fact_id",
)
_URL = "https://example.com/synthetic/evidence?ref=1"
_NOTE = "Synthetic note: see https://example.com/elsewhere for context."


def _tool_args(action_id: UUID, subject: UUID, obj: UUID) -> dict[str, Any]:
    return {
        "subject": str(subject),
        "predicate": "works-at",
        "object": str(obj),
        "object_kind": "entity",
        **dict.fromkeys(_TEMPORAL_KEYS),
        "conf": 1.0,
        "approval_action_id": str(action_id),
    }


class _World:
    """One parked external proposal in the ``relationship`` schema, as fake rows."""

    def __init__(self) -> None:
        self.action_id = uuid4()
        self.proposal_id = uuid4()
        self.subject = uuid4()
        self.object = uuid4()
        self.action: dict[str, Any] = {
            "id": self.action_id,
            "tool_name": "relationship_assert_fact",
            "tool_args": _tool_args(self.action_id, self.subject, self.object),
            "status": "pending",
            "requested_at": datetime.now(UTC),
            "expires_at": datetime.now(UTC) + timedelta(days=14),
            "agent_summary": "External proposal: relationship fact 'works-at'",
            "why": "An external AI client proposed a 'works-at' relationship.",
            "evidence": [
                {"type": "text", "ref": "external_mcp:client=synthetic-client", "note": "x"},
                {"type": "text", "ref": "external_mcp:url_host=https://example.com", "note": "y"},
            ],
            "deduplication_key": f"external_mcp:{self.proposal_id}",
            "goal_id": None,
        }
        self.proposal: dict[str, Any] | None = {
            "state": "parked",
            "note": _NOTE,
            "evidence_url": _URL,
            "subject_entity_id": self.subject,
            "predicate": "works-at",
            "object_entity_id": self.object,
        }
        self.entities: dict[UUID, dict[str, Any]] = {
            self.subject: {
                "id": self.subject,
                "canonical_name": "Synthetic Person A",
                "entity_type": "person",
                "merged_into": None,
                "deleted_at": None,
                "tombstone": None,
            },
            self.object: {
                "id": self.object,
                "canonical_name": "Synthetic Org B",
                "entity_type": "organization",
                "merged_into": None,
                "deleted_at": None,
                "tombstone": None,
            },
        }
        self.events: list[str] = []

    @property
    def digest(self) -> str:
        return args_digest(self.action["tool_args"])


class _Conn:
    _audit_depth = 0

    def __init__(self, world: _World) -> None:
        self.world = world

    def is_in_transaction(self) -> bool:
        return self._audit_depth > 0

    @asynccontextmanager
    async def transaction(self):
        self._audit_depth += 1
        before = deepcopy(self.world.action)
        self.world.events.append("begin")
        try:
            yield
        except BaseException:
            self.world.action = before
            self.world.events.append("rollback")
            raise
        else:
            self.world.events.append("commit")
        finally:
            self._audit_depth -= 1

    async def fetchval(self, sql: str, *args: Any) -> Any:
        assert "to_regclass" in sql
        return False

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        if "relationship.external_fact_proposals" in sql:
            assert args == (self.world.proposal_id, self.world.action_id)
            return deepcopy(self.world.proposal)
        if sql.startswith("SELECT deduplication_key FROM pending_actions"):
            return {"deduplication_key": self.world.action["deduplication_key"]}
        if "FOR UPDATE" in sql:
            self.world.events.append("lock")
        return deepcopy(self.world.action)

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        assert "public.entities" in sql
        return [deepcopy(self.world.entities[i]) for i in args[0] if i in self.world.entities]


@pytest.fixture
def world() -> _World:
    return _World()


@pytest.fixture
def http(app, monkeypatch, world):
    conn = _Conn(world)

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield conn

    pool = Pool()
    world.pool = pool  # type: ignore[attr-defined]

    async def find(*_args):
        return "relationship", pool

    async def named(*_args):
        return [("relationship", pool)]

    async def no_card(*_args):
        return None

    async def approve(actual_conn, **kwargs):
        world.events.append("approve")
        world.approval_kwargs = kwargs  # type: ignore[attr-defined]
        if world.action["status"] != "pending":
            return {"error": "cannot transition decided action"}
        world.action["status"] = "approved"
        row = deepcopy(world.action)
        row["id"] = str(row["id"])
        return row

    async def dispatch(*args):
        world.events.append("dispatch")
        return routes._DispatchOutcome(kind="unreachable")

    async def audit(*args, **kwargs):
        world.events.append("audit")

    monkeypatch.setattr(routes, "_find_action_pool", find)
    monkeypatch.setattr(routes, "_find_named_approvals_pools", named)
    monkeypatch.setattr(routes, "_dashboard_owner_delivery_card", no_card)
    monkeypatch.setattr(routes, "_decision_memory_writer_for", lambda *a: None)
    monkeypatch.setattr(routes.approvals_ops, "approve_action", approve)
    monkeypatch.setattr(routes.audit_router, "append", audit)
    monkeypatch.setattr(routes, "_dispatch_approved_action_outcome", dispatch)

    class Db:
        butler_names: list[str] = []

    app.dependency_overrides[get_db] = lambda: Db()
    app.dependency_overrides[routes.get_mcp_manager] = lambda: object()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _detail(client, world) -> dict[str, Any]:
    async with client:
        response = await client.get(f"/api/approvals/{world.action_id}")
    assert response.status_code == 200, response.text
    return response.json()["data"]


# REQ-dashboard-approvals-904, "The owner sees who and what before approving".
@pytest.mark.parametrize("legacy", [False, True])
async def test_detail_returns_typed_review_and_no_raw_tool_args(http, world, legacy) -> None:
    if not legacy:
        from butlers.modules.approvals.external_proposals import external_proposal_evidence

        world.action["evidence"] = list(external_proposal_evidence("synthetic-client", _URL))
    detail = await _detail(http, world)
    review = detail["external_proposal_review"]
    assert review["proposal_id"] == str(world.proposal_id)
    assert review["client_label"] == "synthetic-client"
    assert review["evidence_url"] == _URL
    assert review["predicate"] == "works-at"
    assert review["subject"] == {
        "id": str(world.subject),
        "name": "Synthetic Person A",
        "entity_type": "person",
        "status": "live",
    }
    assert review["object"] == {
        "id": str(world.object),
        "name": "Synthetic Org B",
        "entity_type": "organization",
        "status": "live",
    }
    assert review["authority_statement"] == review_mod.THIRD_PARTY_AUTHORITY_STATEMENT
    assert "third-party" in review["authority_statement"]
    assert review["unresolvable_entity"] is False
    assert review["approve_unavailable_reason"] is None
    assert review["displayed_call"] == {
        "tool_name": "relationship_assert_fact",
        "args_digest": world.digest,
    }
    # No raw tool_args dump anywhere in the dossier, and no generic preview an older
    # client or a summary surface could approve from.
    assert "tool_args" not in detail["proposed_action"]
    assert detail["call_preview"] is None
    assert '"conf"' not in str(detail) and "approval_action_id" not in str(detail)


# REQ-dashboard-approvals-904, "Evidence links are inert": data, returned verbatim for
# plain-text rendering; the API neither shortens nor turns them into markup.
async def test_detail_returns_note_and_evidence_url_as_data(http, world) -> None:
    review = (await _detail(http, world))["external_proposal_review"]
    assert review["evidence_url"] == _URL
    assert review["note"] == _NOTE


async def test_ordinary_relationship_fact_action_keeps_no_review(http, world) -> None:
    """Scope: only an ``external_mcp:`` action earns the typed review."""
    world.action["deduplication_key"] = None
    detail = await _detail(http, world)
    assert detail["external_proposal_review"] is None
    assert detail["call_preview"] is None
    assert "tool_args" in detail["proposed_action"]


# REQ-dashboard-approvals-904, "Approval submits the displayed call".
@pytest.mark.parametrize("legacy", [False, True])
async def test_approve_with_displayed_call_executes(http, world, legacy) -> None:
    displayed = (await _detail(http, world))["external_proposal_review"]["displayed_call"]
    world.events.clear()
    path = f"/api/approvals/{'actions/' if legacy else ''}{world.action_id}/approve"
    async with httpx.AsyncClient(transport=http._transport, base_url="http://test") as client:
        response = await client.post(path, json={"displayed_call": displayed})
    assert response.status_code == 200, response.text
    assert world.events[:2] == ["begin", "lock"]
    assert "approve" in world.events and "dispatch" in world.events
    assert world.approval_kwargs["create_rule"] is False  # type: ignore[attr-defined]
    # The owning write receives the binding to exactly the reviewed call.
    assert world.approval_kwargs["external_review"] == ExternalReviewBinding(  # type: ignore[attr-defined]
        world.action_id, displayed["args_digest"]
    )


# A dashboard card token (owner-delivery rollout) decides through the same check and
# hands the owning write the same binding.
async def test_owner_delivery_token_approve_carries_the_review_binding(
    http, world, monkeypatch
) -> None:
    displayed = (await _detail(http, world))["external_proposal_review"]["displayed_call"]

    async def redeem(conn, _parsed_id, _stored, *, decide, **_kwargs):
        world.events.append("redeem")
        return await decide(conn, "approve")

    monkeypatch.setattr(routes, "_redeem_owner_delivery", redeem)
    world.events.clear()
    await routes.approve_owning_action(
        parsed_id=world.action_id,
        actor_id="synthetic-owner",
        edits=None,
        action_butler="relationship",
        target_pool=world.pool,  # type: ignore[attr-defined]
        db_mgr=SimpleNamespace(),  # type: ignore[arg-type]
        mcp_mgr=object(),  # type: ignore[arg-type]
        displayed_call=DisplayedApprovalCall(**displayed),
        require_displayed_call=True,
        owner_delivery_token="synthetic-token",
    )
    assert "redeem" in world.events and "approve" in world.events
    assert world.approval_kwargs["external_review"] == ExternalReviewBinding(  # type: ignore[attr-defined]
        world.action_id, displayed["args_digest"]
    )


@pytest.mark.parametrize("legacy", [False, True])
async def test_changed_call_is_refused_without_execution(http, world, legacy) -> None:
    displayed = (await _detail(http, world))["external_proposal_review"]["displayed_call"]
    world.action["tool_args"] = {**world.action["tool_args"], "predicate": "knows"}
    world.proposal["predicate"] = "knows"  # type: ignore[index]
    world.events.clear()
    path = f"/api/approvals/{'actions/' if legacy else ''}{world.action_id}/approve"
    async with httpx.AsyncClient(transport=http._transport, base_url="http://test") as client:
        response = await client.post(path, json={"displayed_call": displayed})
    assert response.status_code == 409
    assert world.events == ["begin", "lock", "rollback"]
    assert world.action["status"] == "pending"


async def test_approve_without_displayed_call_is_refused(http, world) -> None:
    async with http:
        response = await http.post(f"/api/approvals/{world.action_id}/approve", json={})
    assert response.status_code == 409
    assert "approve" not in world.events and "dispatch" not in world.events


# REQ-dashboard-approvals-904, "No rule can be created from the review".
async def test_create_rule_is_refused(http, world) -> None:
    displayed = (await _detail(http, world))["external_proposal_review"]["displayed_call"]
    async with httpx.AsyncClient(transport=http._transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/approvals/actions/{world.action_id}/approve",
            json={"displayed_call": displayed, "create_rule": True},
        )
    assert response.status_code == 409
    assert response.json()["detail"] == EXTERNAL_PROPOSAL_RULE_REFUSAL
    assert "approve" not in world.events


# REQ-dashboard-approvals-904, "Unresolvable entity blocks approval but not rejection"
# (the reject half runs on PostgreSQL in the relationship DB suite).
@pytest.mark.parametrize(
    ("which", "change", "status"),
    [
        ("subject", {"merged_into": "00000000-0000-0000-0000-00000000000a"}, "merged"),
        ("subject", {"deleted_at": "2026-11-01T00:00:00Z"}, "deleted"),
        ("subject", {"tombstone": "true"}, "deleted"),
        ("object", {"merged_into": "00000000-0000-0000-0000-00000000000b"}, "merged"),
        ("object", None, "missing"),
    ],
)
async def test_unresolvable_entity_disables_approve(http, world, which, change, status) -> None:
    shown = {"tool_name": "relationship_assert_fact", "args_digest": world.digest}
    entity_id = world.subject if which == "subject" else world.object
    if change is None:
        del world.entities[entity_id]
    else:
        world.entities[entity_id].update(change)
    review = (await _detail(http, world))["external_proposal_review"]
    assert review["unresolvable_entity"] is True
    assert review[which]["status"] == status
    assert review["displayed_call"] is None
    reason = review["approve_unavailable_reason"]
    assert reason and status in reason and which in reason.lower()
    world.events.clear()
    async with httpx.AsyncClient(transport=http._transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/approvals/{world.action_id}/approve", json={"displayed_call": shown}
        )
    assert response.status_code == 409
    assert response.json()["detail"] == reason
    assert "approve" not in world.events and "dispatch" not in world.events


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda w: w.action["tool_args"].update(conf=0.5), id="conf"),
        pytest.param(lambda w: w.action["tool_args"].update(weight=3), id="unshown-arg"),
        pytest.param(
            lambda w: w.action["tool_args"].update(effective_from="2026-01-01"), id="temporal"
        ),
        pytest.param(lambda w: w.action["tool_args"].update(object_kind="text"), id="literal"),
        pytest.param(
            lambda w: w.action["tool_args"].update(approval_action_id=str(uuid4())), id="other-id"
        ),
        pytest.param(lambda w: w.proposal.update(state="applied"), id="not-parked"),
        pytest.param(lambda w: setattr(w, "proposal", None), id="no-record"),
        pytest.param(lambda w: w.proposal.update(object_entity_id=uuid4()), id="mismatch"),
        pytest.param(lambda w: w.action.update(evidence=[]), id="no-client-label"),
    ],
)
async def test_unshowable_or_unbound_call_has_no_displayed_call(http, world, mutate) -> None:
    mutate(world)
    shown = {"tool_name": "relationship_assert_fact", "args_digest": world.digest}
    review = (await _detail(http, world))["external_proposal_review"]
    assert review["displayed_call"] is None
    assert review["approve_unavailable_reason"]
    async with httpx.AsyncClient(transport=http._transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/approvals/{world.action_id}/approve", json={"displayed_call": shown}
        )
    assert response.status_code == 409
    assert "approve" not in world.events


async def test_connector_callback_cannot_approve_an_external_proposal(
    http, world, monkeypatch
) -> None:
    """External proposals are never pushed; only the typed dashboard review approves one."""

    async def run(conn, **kwargs):
        return await routes.approve_owning_action(
            parsed_id=world.action_id,
            actor_id="connector:synthetic",
            edits=None,
            action_butler="relationship",
            target_pool=pool,
            db_mgr=object(),  # type: ignore[arg-type]
            mcp_mgr=object(),  # type: ignore[arg-type]
            check_presented_digests=True,
            require_displayed_call=False,
        )

    conn = _Conn(world)

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield conn

    pool = Pool()
    with pytest.raises(routes.HTTPException) as refused:
        await run(conn)
    assert refused.value.status_code == 409
    # Not "the call changed": it is only ever approvable from the dashboard review.
    assert refused.value.detail == routes.EXTERNAL_PROPOSAL_REVIEW_REQUIRED
    assert "approve" not in world.events


async def test_edits_are_refused_even_with_the_displayed_call(http, world) -> None:
    """An edit would execute arguments no review showed (REQ-dashboard-approvals-904)."""
    displayed = (await _detail(http, world))["external_proposal_review"]["displayed_call"]
    stored = deepcopy(world.action["tool_args"])
    async with httpx.AsyncClient(transport=http._transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/approvals/{world.action_id}/approve",
            json={"displayed_call": displayed, "edits": {"subject": str(uuid4())}},
        )
    assert response.status_code == 409
    assert response.json()["detail"] == routes.EXTERNAL_PROPOSAL_EDIT_REFUSAL
    assert world.action["tool_args"] == stored
    assert "approve" not in world.events and "dispatch" not in world.events


async def test_malformed_stored_args_fail_closed_in_the_reader(world) -> None:
    """The approve path hands the reader the raw locked row; bad JSON is unapprovable."""
    world.action["tool_args"] = "{not json"
    review = await review_mod.read_external_proposal_review(
        _Conn(world), action_id=world.action_id, row=world.action
    )
    assert review.displayed_call is None
    assert review.approve_unavailable_reason


async def test_dossier_does_not_resolve_entities_twice(http, world, monkeypatch) -> None:
    async def fail(*_args):
        raise AssertionError("the typed review already names the entities")

    monkeypatch.setattr(routes, "_resolve_referenced_entities", fail)
    assert (await _detail(http, world))["referenced_entities"] == []


def test_temporal_keys_match_the_relationship_wire_keys() -> None:
    """A new wire key must be shown or refused deliberately, not make every review fail."""
    from butlers.roster_imports import roster_module

    temporal = roster_module("relationship", "tools/fact_temporal")
    assert review_mod.TEMPORAL_KEYS == frozenset(temporal.WIRE_KEYS)
