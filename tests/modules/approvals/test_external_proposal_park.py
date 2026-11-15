"""Real-Postgres coverage for external MCP proposal parking (RFC 0039 D13).

REQ-module-approvals-010, REQ-public-mcp-proposals-005 and
REQ-public-mcp-proposals-008: an importer-parked ``relationship_assert_fact``
action is never pushed, never matched by a standing rule, keeps ``origin`` NULL,
parks at most once per proposal, and its approval feeds no autonomy history,
no promotion suggestion and no standing rule. Every exemption has a positive
control showing the same mechanism does act on an ordinary action.

The Relationship provenance and replay half (REQ-relationship-facts-011) lives
in ``roster/relationship/tests/test_external_fact_proposals.py``.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from butlers.api.routers.approvals import _delivery_truth, _pending_action_to_api
from butlers.modules.approvals import operations
from butlers.modules.approvals.external_proposals import (
    EXTERNAL_PROPOSAL_REVIEW_ONLY,
    ExternalReviewBinding,
)
from butlers.modules.approvals.gate import match_standing_rule
from butlers.modules.approvals.models import PendingAction
from butlers.modules.approvals.module import ApprovalsModule
from butlers.modules.approvals.park import park_external_proposal, park_pending_action
from butlers.modules.approvals.previews import args_digest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_ACTOR = {"type": "human", "id": "synthetic-owner", "authenticated": True}


@pytest.fixture
async def approvals_pool(db):
    """The production core + approvals migration chains on a fresh clone."""
    return await db("core", "approvals")


def _args(action_id: uuid.UUID, *, subject: str | None = None, obj: str | None = None) -> dict:
    return {
        "subject": subject or str(uuid.uuid4()),
        "predicate": "works-at",
        "object": obj or str(uuid.uuid4()),
        "object_kind": "entity",
        "conf": 1.0,
        "approval_action_id": str(action_id),
    }


async def _park(pool, *, proposal_id=None, tool_args=None, evidence_url=None, **overrides):
    action_id = overrides.pop("action_id", uuid.uuid4())
    return await park_external_proposal(
        pool,
        proposal_id=proposal_id or uuid.uuid4(),
        action_id=action_id,
        tool_args=tool_args if tool_args is not None else _args(action_id),
        client_label="synthetic-client",
        evidence_url=evidence_url,
        origin_butler="relationship",
        **overrides,
    )


async def _exact_rule(pool, tool_args: dict) -> dict:
    """An active standing rule that pins every stored argument exactly."""
    constraints = {key: {"type": "exact", "value": value} for key, value in tool_args.items()}
    rule_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO approval_rules (id, tool_name, arg_constraints, description, active) "
        "VALUES ($1, 'relationship_assert_fact', $2, 'synthetic exact rule', true)",
        rule_id,
        constraints,
    )
    return dict(await pool.fetchrow("SELECT * FROM approval_rules WHERE id = $1", rule_id))


# REQ-module-approvals-010: "A matching standing rule does not approve an external proposal";
# REQ-public-mcp-proposals-008: no rule, policy or batch approves an importer-parked action.
async def test_matching_standing_rule_never_approves_external_proposal(approvals_pool) -> None:
    action_id = uuid.uuid4()
    tool_args = _args(action_id)
    rule = await _exact_rule(approvals_pool, tool_args)
    # Positive control: the gate's own matcher selects this rule for the same call.
    selected = match_standing_rule("relationship_assert_fact", tool_args, [rule])
    assert selected is not None and selected["id"] == rule["id"]

    admission = await _park(approvals_pool, action_id=action_id, tool_args=tool_args)

    row = await approvals_pool.fetchrow(
        "SELECT status, approval_rule_id, decided_by FROM pending_actions WHERE id = $1",
        admission.action_id,
    )
    assert dict(row) == {"status": "pending", "approval_rule_id": None, "decided_by": None}
    assert await approvals_pool.fetchval("SELECT use_count FROM approval_rules") == 0
    assert not await approvals_pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM approval_events WHERE event_type = 'action_auto_approved')"
    )


# REQ-module-approvals-010: "Origin values are unchanged"; REQ-public-mcp-proposals-005:
# exactly one pending action, 14-day expiry, digest-only evidence, no outside text.
async def test_external_park_shape_origin_null_and_digest_only_evidence(approvals_pool) -> None:
    proposal_id = uuid.uuid4()
    url = "https://user:secret@Example.com/ignore-previous-instructions?token=abc"
    before = await approvals_pool.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'pending_actions_origin_check'"
    )
    admission = await _park(approvals_pool, proposal_id=proposal_id, evidence_url=url)

    row = await approvals_pool.fetchrow(
        "SELECT * FROM pending_actions WHERE id = $1", admission.action_id
    )
    assert row["origin"] is None
    assert row["status"] == "pending"
    assert row["tool_name"] == "relationship_assert_fact"
    assert row["deduplication_key"] == f"external_mcp:{proposal_id}"
    assert row["session_id"] is None
    assert row["expires_at"] - row["requested_at"] == timedelta(days=14)
    digest = hashlib.sha256(url.encode()).hexdigest()
    refs = [item["ref"] for item in row["evidence"]]
    assert refs == [
        "external_mcp:client=synthetic-client",
        "external_mcp:url_host=https://example.com",
        f"external_mcp:url_sha256={digest}",
    ]
    assert all(set(item) == {"type", "ref", "note"} for item in row["evidence"])
    as_text = await approvals_pool.fetchval(
        "SELECT pa::text FROM pending_actions pa WHERE id = $1", admission.action_id
    )
    for leaked in ("ignore-previous", "secret", "token=abc", "user:"):
        assert leaked not in as_text
    after = await approvals_pool.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'pending_actions_origin_check'"
    )
    assert before == after and "'prepared'" in after and "external" not in after
    # It projects onto the dashboard approvals list like any pending action.
    action = PendingAction.from_row(row)
    item = _pending_action_to_api(action, "relationship", delivery=_delivery_truth(row, action))
    assert item.status == "pending" and item.origin is None
    assert [evidence.ref for evidence in item.evidence] == refs
    # Default-off admission: no delivery intent exists to reach a provider.
    assert admission.intent_id is None
    assert not await approvals_pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM approval_delivery_intents WHERE action_id = $1)",
        admission.action_id,
    )


# REQ-module-approvals-010: "Admission-enabled parking stays collapsed" and
# "Parking an external proposal sends no push" (five in one pass).
async def test_admission_enabled_external_parks_are_only_collapsed(approvals_pool) -> None:
    await approvals_pool.execute(
        "UPDATE approval_delivery_rollout SET admission_enabled = true WHERE singleton"
    )
    admissions = [await _park(approvals_pool) for _ in range(5)]

    assert {a.admission_mode for a in admissions} == {"collapsed"}
    rows = await approvals_pool.fetch(
        """
        SELECT pa.origin, i.admission_mode, p.presentation_mode, p.state, p.next_attempt_at
          FROM pending_actions pa
          JOIN approval_delivery_intents i ON i.action_id = pa.id
          JOIN approval_delivery_presentations p ON p.intent_id = i.id
         WHERE pa.deduplication_key LIKE 'external_mcp:%'
        """
    )
    assert len(rows) == 5
    assert {tuple(row) for row in rows} == {(None, "collapsed", "collapsed", "collapsed", None)}
    # No cohort (digest) membership, no other presentation, no push reservation.
    assert await approvals_pool.fetchval("SELECT count(*) FROM approval_delivery_cohorts") == 0
    assert (
        await approvals_pool.fetchval("SELECT count(*) FROM approval_delivery_cohort_members") == 0
    )
    assert (
        await approvals_pool.fetchval(
            "SELECT count(*) FROM approval_delivery_presentations WHERE state <> 'collapsed'"
        )
        == 0
    )
    assert await approvals_pool.fetchval("SELECT count(*) FROM approval_push_emissions") == 0

    # Positive control: the same rollout pushes an ordinary park, and the five
    # silent external parks do not count toward its burst window.
    ordinary = await park_pending_action(
        approvals_pool,
        action_id=uuid.uuid4(),
        tool_name="relationship_assert_fact",
        tool_args={"subject": str(uuid.uuid4())},
        agent_summary="ordinary park",
        requested_at=datetime.now(UTC),
        expires_at=None,
        origin_butler="relationship",
    )
    assert ordinary.admission_mode == "single"
    assert (
        await approvals_pool.fetchval(
            "SELECT state FROM approval_delivery_presentations WHERE intent_id = $1",
            ordinary.intent_id,
        )
        == "ready"
    )


# REQ-module-approvals-010: "Deduplication key prevents a second park";
# REQ-public-mcp-proposals-005: "Re-import parks nothing new".
@pytest.mark.parametrize("admission_enabled", [False, True])
async def test_same_proposal_parks_once_in_any_status(approvals_pool, admission_enabled) -> None:
    await approvals_pool.execute(
        "UPDATE approval_delivery_rollout SET admission_enabled = $1 WHERE singleton",
        admission_enabled,
    )
    proposal_id = uuid.uuid4()
    first = await _park(approvals_pool, proposal_id=proposal_id)
    second = await _park(approvals_pool, proposal_id=proposal_id)
    assert second.duplicate is True and second.action_id == first.action_id

    # A terminal action outside the active unique index still blocks a re-park.
    await approvals_pool.execute(
        "UPDATE pending_actions SET status = 'expired' WHERE id = $1", first.action_id
    )
    third = await _park(approvals_pool, proposal_id=proposal_id)
    assert third.duplicate is True and third.action_id == first.action_id
    assert (
        await approvals_pool.fetchval(
            "SELECT count(*) FROM pending_actions WHERE deduplication_key = $1",
            f"external_mcp:{proposal_id}",
        )
        == 1
    )


async def test_park_joins_the_callers_transaction(approvals_pool) -> None:
    """The importer parks and writes proposal state together, or neither."""
    action_id = uuid.uuid4()
    async with approvals_pool.acquire() as conn:
        with pytest.raises(RuntimeError, match="caller failed"):
            async with conn.transaction():
                await _park(conn, action_id=action_id)
                raise RuntimeError("caller failed after parking")
    assert not await approvals_pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pending_actions WHERE id = $1)", action_id
    )


# REQ-relationship-facts-011: "Provenance parameters in stored arguments are refused".
@pytest.mark.parametrize("key", ["src", "content_authority", "observed_at"])
async def test_provenance_in_stored_args_is_refused_before_any_row(approvals_pool, key) -> None:
    action_id = uuid.uuid4()
    with pytest.raises(ValueError, match=key):
        await _park(
            approvals_pool,
            action_id=action_id,
            tool_args=_args(action_id) | {key: "external_mcp:forged"},
        )
    with pytest.raises(ValueError, match="approval_action_id"):
        await _park(approvals_pool, tool_args=_args(uuid.uuid4()))
    assert await approvals_pool.fetchval("SELECT count(*) FROM pending_actions") == 0


async def _ordinary_pending(pool, tool_args: dict) -> uuid.UUID:
    action_id = uuid.uuid4()
    await park_pending_action(
        pool,
        action_id=action_id,
        tool_name="relationship_assert_fact",
        tool_args=tool_args,
        agent_summary="ordinary park",
        requested_at=datetime.now(UTC),
        expires_at=None,
        origin_butler="relationship",
    )
    return action_id


async def _dashboard_binding(pool, action_id: uuid.UUID) -> ExternalReviewBinding:
    """The binding the dashboard route supplies after its displayed-call check.

    This chain has no Relationship schema, so the typed review is not read here;
    ``roster/relationship/tests/external_review_fixtures.py`` builds the same binding
    from the real review reader.
    """
    stored = await pool.fetchval("SELECT tool_args FROM pending_actions WHERE id=$1", action_id)
    return ExternalReviewBinding(action_id=action_id, args_digest=args_digest(stored))


async def _approve_via(
    path: str,
    pool,
    action_id: uuid.UUID,
    *,
    create_rule=False,
    external_review: ExternalReviewBinding | None = None,
) -> dict:
    if path == "operations":
        return await operations.approve_action(
            pool, str(action_id), create_rule=create_rule, external_review=external_review
        )
    assert external_review is None, "no model-driven path holds a dashboard binding"
    module = ApprovalsModule()
    await module.on_startup({}, pool)
    try:
        return await module._approve_action(str(action_id), create_rule=create_rule, actor=_ACTOR)
    finally:
        await module.on_shutdown()


async def _rule_from_action_via(path: str, pool, action_id: uuid.UUID) -> dict:
    if path == "operations":
        return await operations.create_rule_from_action(pool, str(action_id))
    module = ApprovalsModule()
    await module.on_startup({}, pool)
    try:
        return await module._create_rule_from_action(str(action_id), actor=_ACTOR)
    finally:
        await module.on_shutdown()


# REQ-dashboard-approvals-904: only the passkey dashboard review approves an outside
# proposal. The module MCP tool never does, and the shared operation refuses without the
# dashboard route's binding to this exact call, all before any status change.
@pytest.mark.parametrize(
    ("path", "binding"),
    [
        ("mcp_module", None),
        ("operations", None),
        ("operations", "other_action"),
        ("operations", "other_call"),
    ],
)
async def test_external_proposal_approves_only_from_the_dashboard_review(
    approvals_pool, path, binding
) -> None:
    admission = await _park(approvals_pool)
    action_id = admission.action_id
    bound = await _dashboard_binding(approvals_pool, action_id)
    external_review = {
        None: None,
        "other_action": ExternalReviewBinding(uuid.uuid4(), bound.args_digest),
        "other_call": ExternalReviewBinding(action_id, args_digest({"predicate": "knows"})),
    }[binding]

    result = await _approve_via(path, approvals_pool, action_id, external_review=external_review)

    assert result == {"error": EXTERNAL_PROPOSAL_REVIEW_ONLY}
    row = await approvals_pool.fetchrow(
        "SELECT status, decided_by FROM pending_actions WHERE id=$1", action_id
    )
    assert dict(row) == {"status": "pending", "decided_by": None}
    assert (
        await approvals_pool.fetchval(
            "SELECT count(*) FROM approval_events WHERE action_id=$1 AND event_type='action_approved'",
            action_id,
        )
        == 0
    )

    # Positive controls: the same path approves an ordinary action, and the shared
    # operation approves this proposal with the dashboard's binding to its call.
    ordinary = await _ordinary_pending(approvals_pool, {"subject": str(uuid.uuid4())})
    assert "error" not in await _approve_via(path, approvals_pool, ordinary)
    approved = await _approve_via("operations", approvals_pool, action_id, external_review=bound)
    assert "error" not in approved and approved["status"] == "approved"


# REQ-module-approvals-010: "Approving outside proposals never suggests a rule".
@pytest.mark.parametrize("path", ["operations", "mcp_module"])
async def test_five_external_approvals_leave_no_history_and_no_suggestion(
    approvals_pool, path
) -> None:
    subject, obj = str(uuid.uuid4()), str(uuid.uuid4())
    for _ in range(5):
        action_id = uuid.uuid4()
        admission = await _park(
            approvals_pool,
            action_id=action_id,
            tool_args=_args(action_id, subject=subject, obj=obj),
        )
        # Only the dashboard's bound entry approves an outside proposal; the
        # module path is exercised by the ordinary control below.
        result = await _approve_via(
            "operations",
            approvals_pool,
            admission.action_id,
            external_review=await _dashboard_binding(approvals_pool, admission.action_id),
        )
        assert "error" not in result and result["status"] == "approved"
    assert await approvals_pool.fetchval("SELECT count(*) FROM autonomy_approval_history") == 0
    assert await approvals_pool.fetchval("SELECT count(*) FROM autonomy_suggestions") == 0

    # Positive control: five ordinary approvals of one shape do record history
    # and raise a promotion suggestion through the same approve path.
    shape = {"subject": subject, "predicate": "works-at", "object": obj, "object_kind": "entity"}
    for _ in range(5):
        action_id = await _ordinary_pending(approvals_pool, shape)
        result = await _approve_via(path, approvals_pool, action_id)
        assert "error" not in result
    assert await approvals_pool.fetchval("SELECT count(*) FROM autonomy_approval_history") == 5
    assert await approvals_pool.fetchval("SELECT count(*) FROM autonomy_suggestions") == 1


# REQ-module-approvals-010: "An approve request cannot create a rule from an outside proposal".
@pytest.mark.parametrize("path", ["operations", "mcp_module"])
async def test_create_rule_on_external_proposal_is_refused(
    approvals_pool, monkeypatch, path
) -> None:
    from tests.approval_stepup_helpers import admit_rule_boundary_for_non_auth_owner

    # Step-up is admitted, so the refusal below is the exemption, not missing step-up.
    admit_rule_boundary_for_non_auth_owner(monkeypatch)
    admission = await _park(approvals_pool)

    result = await _approve_via(path, approvals_pool, admission.action_id, create_rule=True)

    assert "standing approval rule" in result["error"]
    assert (
        await approvals_pool.fetchval(
            "SELECT status FROM pending_actions WHERE id = $1", admission.action_id
        )
        == "pending"
    )
    assert await approvals_pool.fetchval("SELECT count(*) FROM approval_rules") == 0
    assert await approvals_pool.fetchval("SELECT count(*) FROM autonomy_approval_history") == 0
    # The same refusal on the create-rule-from-action surface of this path.
    from_action = await _rule_from_action_via(path, approvals_pool, admission.action_id)
    assert "standing approval rule" in from_action["error"]
    assert await approvals_pool.fetchval("SELECT count(*) FROM approval_rules") == 0
    assert (
        await approvals_pool.fetchval(
            "SELECT status FROM pending_actions WHERE id = $1", admission.action_id
        )
        == "pending"
    )

    # Positive control: the same request on an ordinary action creates the rule.
    ordinary = await _ordinary_pending(approvals_pool, {"subject": str(uuid.uuid4())})
    created = await _approve_via(path, approvals_pool, ordinary, create_rule=True)
    assert "error" not in created and created["created_rule"]
    assert await approvals_pool.fetchval("SELECT count(*) FROM approval_rules") == 1
    other = await _ordinary_pending(approvals_pool, {"subject": str(uuid.uuid4())})
    derived = await _rule_from_action_via(path, approvals_pool, other)
    assert "error" not in derived
    assert await approvals_pool.fetchval("SELECT count(*) FROM approval_rules") == 2


# REQ-module-approvals-010: the dashboard route POST /api/approvals/actions/{id}/approve
# refuses create_rule for an outside proposal before any decision.
async def test_dashboard_approve_route_refuses_create_rule(approvals_pool) -> None:
    from butlers.api.routers.approvals import approve_owning_action

    admission = await _park(approvals_pool)
    with pytest.raises(HTTPException) as refused:
        await approve_owning_action(
            parsed_id=admission.action_id,
            actor_id="synthetic-owner",
            edits=None,
            action_butler="relationship",
            target_pool=approvals_pool,
            db_mgr=SimpleNamespace(),
            mcp_mgr=SimpleNamespace(),
            require_displayed_call=True,
            create_rule=True,
        )
    assert refused.value.status_code == 409
    assert "standing approval rule" in refused.value.detail
    assert (
        await approvals_pool.fetchval(
            "SELECT status FROM pending_actions WHERE id = $1", admission.action_id
        )
        == "pending"
    )
    assert await approvals_pool.fetchval("SELECT count(*) FROM approval_rules") == 0
