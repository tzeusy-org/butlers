"""Real-PostgreSQL proof of canonical owner delivery (bu-dhloi1.13, RFC 0038 S6).

Spec: REQ-owner-delivery-001..004. One subject per approval, a presentation per surface,
single-use tokens bound to subject/action/digest/owner/surface/generation/expiry; the
winning action stales every competing token in the authority's own transaction;
ambiguous provider results and failed retractions stay honest.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.deps import get_db
from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.api.owner_auth.http import OwnerAuthMiddleware
from butlers.api.routers import approvals as approvals_router
from butlers.api.routers import owner_delivery as owner_delivery_router
from butlers.core import owner_delivery as od
from butlers.core.approval_callbacks import APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER
from butlers.core.approval_delivery_worker import (
    ApprovalDeliveryWorker,
    DeliveryClaim,
    HandoffResult,
)
from butlers.modules.approvals import operations as approvals_ops
from butlers.modules.approvals.delivery_lifecycle import transition_pending_action
from butlers.modules.approvals.delivery_recovery import (
    ApprovalDeliveryRenderer,
    ApprovalDeliveryRepository,
)
from butlers.modules.approvals.models import ActionStatus
from butlers.modules.approvals.owner_delivery_bridge import (
    approval_digests,
    bridge_existing,
    open_approval_subject,
    parity,
)
from butlers.modules.approvals.park import park_pending_action
from butlers.testing.db_manager import db_manager
from tests.api.auth_helpers import _DomainOwnerState
from tests.audit_helpers import clear_audit_log_for_test

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_RECIPIENT = "9001"
_CONNECTOR_TOKEN = "test-only-callback-connector-token"
_DASHBOARD_API_KEY = "test-only-dashboard-api-key"


@pytest.fixture
async def pool(db) -> asyncpg.Pool:
    pool = await db("core", "approvals", max_size=8)
    await clear_audit_log_for_test(pool)
    await pool.execute(
        "UPDATE public.approvals_policy SET quiet_start_hour = NULL, "
        "quiet_end_hour = NULL, timezone = 'UTC' WHERE id = 1"
    )
    await pool.execute(
        "UPDATE approval_delivery_rollout SET admission_enabled = true, worker_enabled = false"
    )
    if await pool.fetchval("SELECT count(*) FROM public.entities WHERE 'owner' = ANY(roles)") == 0:
        await pool.execute(
            "INSERT INTO public.entities (canonical_name, entity_type, roles) "
            "VALUES ('Synthetic owner', 'person', ARRAY['owner'])"
        )
    return pool


async def _enable(pool: asyncpg.Pool, enabled: bool = True) -> None:
    await pool.execute("UPDATE public.owner_delivery_rollout SET enabled = $1", enabled)


async def _owner(pool: asyncpg.Pool) -> uuid.UUID:
    async with pool.acquire() as conn:
        owner = await od.owner_entity_id(conn)
    assert owner is not None
    return owner


async def _park(pool: asyncpg.Pool, ordinal: int = 0) -> uuid.UUID:
    now = datetime.now(UTC) + timedelta(microseconds=ordinal)
    admission = await park_pending_action(
        pool,
        action_id=uuid.uuid4(),
        tool_name="relationship_assert_fact",
        tool_args={"ordinal": ordinal},
        agent_summary="Synthetic owner-delivery fixture",
        requested_at=now,
        expires_at=now + timedelta(hours=72),
        why="Synthetic reason",
        evidence=[],
        blast_radius="contact",
        reversibility="compensable",
        origin_butler="relationship",
    )
    return admission.action_id


async def _present(
    pool: asyncpg.Pool, action_id: uuid.UUID, *surfaces: str, sent: bool = True
) -> tuple[od.Subject, dict[str, od.Presentation]]:
    """Open the subject and present it, marking provider surfaces as delivered."""
    out: dict[str, od.Presentation] = {}
    async with pool.acquire() as conn, conn.transaction():
        action = await conn.fetchrow(
            "SELECT * FROM pending_actions WHERE id = $1 FOR UPDATE", action_id
        )
        subject = await open_approval_subject(conn, action)
        for surface in surfaces:
            presentation = await od.present(
                conn,
                subject.id,
                surface=surface,  # type: ignore[arg-type]
                route_ref=_RECIPIENT if surface == "telegram" else "owner:dashboard",
                actions=("approve", "reject", "ack"),
                expires_at=action["expires_at"],
            )
            if surface == "telegram" and sent:
                assert await od.begin_send(conn, presentation.id)
                await od.record_send(conn, presentation.id, outcome="sent", provider_ref="55")
            out[surface] = presentation
    return subject, out


async def _redeem(
    pool: asyncpg.Pool,
    action_id: uuid.UUID,
    token: str,
    *,
    surface: str,
    owner: uuid.UUID | None = None,
    now: datetime | None = None,
    digest: str | None = None,
    calls: list[str] | None = None,
) -> od.Redemption:
    """Redeem inside the authority transaction, deciding through the real approvals ops."""

    async def decide(conn: Any, verb: str) -> dict[str, Any]:
        if calls is not None:
            calls.append(verb)
        op = approvals_ops.approve_action if verb == "approve" else approvals_ops.reject_action
        result = await op(conn, action_id=str(action_id), actor_id="dashboard:rest-api")
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    async with pool.acquire() as conn, conn.transaction():
        action = await conn.fetchrow(
            "SELECT * FROM pending_actions WHERE id = $1 FOR UPDATE", action_id
        )
        _, current = approval_digests(action["tool_name"], action["tool_args"])
        return await od.redeem(
            conn,
            token,
            kind="approval",
            source_ref=str(action_id),
            owner_entity=owner or await od.owner_entity_id(conn),  # type: ignore[arg-type]
            surface=surface,  # type: ignore[arg-type]
            current_action_digest=digest or current,
            decide=decide,
            now=now,
        )


async def _state(pool: asyncpg.Pool, action_id: uuid.UUID) -> tuple[str, str, int]:
    row = await pool.fetchrow(
        "SELECT pa.status, s.state, s.generation FROM pending_actions pa "
        "JOIN public.owner_delivery_subjects s ON s.source_ref = pa.id::text WHERE pa.id = $1",
        action_id,
    )
    return row["status"], row["state"], row["generation"]


async def test_token_wins_once_and_every_competing_token_goes_stale(pool) -> None:
    action_id = await _park(pool)
    _, cards = await _present(pool, action_id, "telegram", "dashboard")
    calls: list[str] = []

    won = await _redeem(
        pool, action_id, cards["telegram"].tokens["approve"], surface="telegram", calls=calls
    )
    assert won.outcome == "won" and won.action == "approve"
    assert calls == ["approve"]
    assert await _state(pool, action_id) == ("approved", "decided", 2)

    # Duplicate callback: same answer, no second effect.
    again = await _redeem(
        pool, action_id, cards["telegram"].tokens["approve"], surface="telegram", calls=calls
    )
    assert again.outcome == "duplicate" and calls == ["approve"]

    # The other surface's buttons are stale, whichever action they carry.
    for verb in ("approve", "reject", "ack"):
        stale = await _redeem(
            pool, action_id, cards["dashboard"].tokens[verb], surface="dashboard", calls=calls
        )
        assert (stale.outcome, stale.reason) == ("refused", "subject_resolved")
    assert calls == ["approve"]

    # The dashboard presentation retracts in the ledger; the sent Telegram card is the
    # retraction target, and stays listed until a retraction is receipted.
    async with pool.acquire() as conn:
        assert [t.provider_ref for t in won.retract] == ["55"]
        listed = await od.pending_retractions(conn, surface="telegram")
        assert [t.presentation_id for t in listed] == [cards["telegram"].id]
        async with conn.transaction():
            await od.record_retraction(
                conn, cards["telegram"].id, retracted=False, reason="provider_error"
            )
        assert len(await od.pending_retractions(conn, surface="telegram")) == 1
        async with conn.transaction():
            await od.record_retraction(conn, cards["telegram"].id, retracted=True)
        assert await od.pending_retractions(conn, surface="telegram") == ()
    kinds = await pool.fetch(
        "SELECT kind FROM public.owner_delivery_receipts ORDER BY created_at, id"
    )
    assert [row["kind"] for row in kinds].count("decided") == 1
    assert {"retract_failed", "retracted"} <= {row["kind"] for row in kinds}
    # Only verifiers are stored; no token text exists anywhere in the ledger.
    dump = await pool.fetchval(
        "SELECT string_agg(t::text, ' ') FROM public.owner_delivery_tokens t"
    )
    assert cards["telegram"].tokens["approve"].split(":", 1)[1] not in dump


@pytest.mark.parametrize(
    "case",
    ["wrong_owner", "wrong_surface", "token_expired", "action_changed", "subject_mismatch"],
)
async def test_bound_tokens_refuse_without_changing_anything(pool, case: str) -> None:
    action_id = await _park(pool)
    other_id = await _park(pool, ordinal=1)
    _, cards = await _present(pool, action_id, "telegram")
    await _present(pool, other_id, "telegram")
    token = cards["telegram"].tokens["approve"]
    kwargs: dict[str, Any] = {"surface": "telegram"}
    target = action_id
    if case == "wrong_owner":
        kwargs["owner"] = uuid.uuid4()
    elif case == "wrong_surface":
        kwargs["surface"] = "dashboard"
    elif case == "token_expired":
        kwargs["now"] = datetime.now(UTC) + timedelta(days=4)
    elif case == "action_changed":
        kwargs["digest"] = "0" * 64
    else:
        target = other_id
    calls: list[str] = []

    refused = await _redeem(pool, target, token, calls=calls, **kwargs)

    assert (refused.outcome, refused.reason) == ("refused", case)
    assert calls == []
    assert await _state(pool, action_id) == ("pending", "delivered", 1)
    assert await pool.fetchval(
        "SELECT consumed_at IS NULL FROM public.owner_delivery_tokens WHERE presentation_id = $1 "
        "AND action = 'approve'",
        cards["telegram"].id,
    )
    # The refusal still works afterwards with the right bindings.
    assert (await _redeem(pool, action_id, token, surface="telegram")).outcome == "won"


async def test_ack_is_not_approval_but_stales_the_presented_actions(pool) -> None:
    action_id = await _park(pool)
    _, cards = await _present(pool, action_id, "telegram", "dashboard")

    ack = await _redeem(pool, action_id, cards["dashboard"].tokens["ack"], surface="dashboard")

    assert ack.outcome == "won"
    assert await _state(pool, action_id) == ("pending", "acknowledged", 2)
    stale = await _redeem(pool, action_id, cards["telegram"].tokens["approve"], surface="telegram")
    assert (stale.outcome, stale.reason) == ("refused", "stale_generation")
    # A fresh presentation at the new generation decides normally.
    _, fresh = await _present(pool, action_id, "telegram")
    assert fresh["telegram"].generation == 2
    won = await _redeem(pool, action_id, fresh["telegram"].tokens["reject"], surface="telegram")
    assert won.outcome == "won"
    assert await _state(pool, action_id) == ("rejected", "decided", 3)


async def test_concurrent_decisions_on_two_surfaces_produce_one_effect(pool) -> None:
    action_id = await _park(pool)
    _, cards = await _present(pool, action_id, "telegram", "dashboard")
    calls: list[str] = []

    results = await asyncio.gather(
        _redeem(
            pool, action_id, cards["telegram"].tokens["approve"], surface="telegram", calls=calls
        ),
        _redeem(
            pool, action_id, cards["dashboard"].tokens["reject"], surface="dashboard", calls=calls
        ),
    )

    assert sorted(r.outcome for r in results) == ["refused", "won"]
    assert len(calls) == 1
    status, state, generation = await _state(pool, action_id)
    assert state == "decided" and generation == 2
    assert status == ("approved" if calls == ["approve"] else "rejected")


async def test_legacy_decision_closes_the_subject_and_stales_every_token(pool) -> None:
    """A decision outside a token (apr1, dashboard, expiry) still fences the cards."""
    action_id = await _park(pool)
    _, cards = await _present(pool, action_id, "telegram")

    transition = await transition_pending_action(
        pool,
        action_id=action_id,
        target_status=ActionStatus.REJECTED,
        decided_by="human:owner@telegram",
        event_actor="human:owner@telegram",
        event_reason="legacy callback",
    )

    assert transition.changed
    assert await _state(pool, action_id) == ("rejected", "decided", 2)
    refused = await _redeem(
        pool, action_id, cards["telegram"].tokens["approve"], surface="telegram"
    )
    assert (refused.outcome, refused.reason) == ("refused", "subject_resolved")
    async with pool.acquire() as conn:
        assert [
            t.presentation_id for t in await od.pending_retractions(conn, surface="telegram")
        ] == [cards["telegram"].id]


async def test_ledger_invariants_hold_for_the_table_owner_too(pool) -> None:
    action_id = await _park(pool)
    subject, cards = await _present(pool, action_id, "telegram")
    denied = asyncpg.InsufficientPrivilegeError
    with pytest.raises(denied):
        await pool.execute(
            "UPDATE public.owner_delivery_subjects SET action_digest = $2 WHERE id = $1",
            subject.id,
            "1" * 64,
        )
    with pytest.raises(denied):
        await pool.execute("DELETE FROM public.owner_delivery_subjects WHERE id = $1", subject.id)
    with pytest.raises(denied):
        await pool.execute("UPDATE public.owner_delivery_receipts SET kind = 'sent'")
    with pytest.raises(denied):
        await pool.execute(
            "UPDATE public.owner_delivery_subjects SET generation = 0 WHERE id = $1", subject.id
        )
    # Tokens cannot be minted for an old generation.
    with pytest.raises(denied):
        await pool.execute(
            "INSERT INTO public.owner_delivery_tokens (verifier, subject_id, owning_schema, "
            "presentation_id, action, action_digest, owner_entity, surface, generation, "
            "expires_at) SELECT $1, id, owning_schema, $2, 'approve', action_digest, "
            "owner_entity, 'telegram', generation + 1, now() + interval '1 hour' "
            "FROM public.owner_delivery_subjects WHERE id = $3",
            "2" * 64,
            cards["telegram"].id,
            subject.id,
        )
    await _redeem(pool, action_id, cards["telegram"].tokens["approve"], surface="telegram")
    with pytest.raises(denied):  # resolved subjects are frozen
        await pool.execute(
            "UPDATE public.owner_delivery_subjects SET state = 'cancelled', decision = NULL "
            "WHERE id = $1",
            subject.id,
        )
    with pytest.raises(denied):  # consumed once
        await pool.execute(
            "UPDATE public.owner_delivery_tokens SET consumed_at = now(), outcome = '{}' "
            "WHERE subject_id = $1 AND consumed_at IS NOT NULL",
            subject.id,
        )
    for table in ("subjects", "presentations", "tokens", "receipts", "source_map", "rollout"):
        assert await pool.fetchval(
            "SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass",
            f"public.owner_delivery_{table}",
        )


async def test_failed_send_revokes_its_tokens_before_presenting_again(pool) -> None:
    action_id = await _park(pool)
    _, cards = await _present(pool, action_id, "telegram", sent=False)
    first = cards["telegram"]
    async with pool.acquire() as conn, conn.transaction():
        assert await od.begin_send(conn, first.id)
        await od.record_send(conn, first.id, outcome="failed")
    _, again = await _present(pool, action_id, "telegram", sent=False)

    assert again["telegram"].id == first.id and again["telegram"].tokens
    revoked = await _redeem(pool, action_id, first.tokens["approve"], surface="telegram")
    assert (revoked.outcome, revoked.reason) == ("refused", "token_revoked")


class _Runtime:
    def __init__(self, result: HandoffResult) -> None:
        self.result = result
        self.envelopes: list[dict[str, Any]] = []

    async def resolve_verified_owner_recipient(self) -> str:
        return _RECIPIENT

    async def resolve_callback_secret(self) -> str:
        return "legacy-callback-secret"

    async def handoff(self, claim: DeliveryClaim, envelope: dict[str, Any]) -> HandoffResult:
        self.envelopes.append(envelope)
        return self.result

    async def reconcile(self, claim: DeliveryClaim) -> HandoffResult:
        return HandoffResult("ambiguous", "provider_outcome_unknown")


def _card_tokens(envelope: dict[str, Any]) -> list[str]:
    return [a["callback_token"] for a in envelope["actions"] if "callback_token" in a]


async def _deliver(pool: asyncpg.Pool, result: HandoffResult) -> _Runtime:
    runtime = _Runtime(result)
    worker = ApprovalDeliveryWorker(
        ApprovalDeliveryRepository(pool), ApprovalDeliveryRenderer(), runtime
    )
    assert await worker.process_one() is True
    return runtime


async def test_worker_sends_canonical_tokens_only_when_enabled(pool) -> None:
    await _park(pool)
    runtime = await _deliver(pool, HandoffResult("confirmed", provider_reference="77"))
    assert all(t.startswith("apr1:") for t in _card_tokens(runtime.envelopes[0]))
    assert await pool.fetchval("SELECT count(*) FROM public.owner_delivery_subjects") == 0

    await _enable(pool)
    action_id = await _park(pool, ordinal=1)
    runtime = await _deliver(pool, HandoffResult("confirmed", provider_reference="78"))

    tokens = _card_tokens(runtime.envelopes[0])
    assert len(tokens) == 2 and all(t.startswith("od1:") for t in tokens)
    row = await pool.fetchrow(
        "SELECT p.state, p.provider_ref, p.route_ref, s.state AS subject_state "
        "FROM public.owner_delivery_presentations p "
        "JOIN public.owner_delivery_subjects s ON s.id = p.subject_id WHERE s.source_ref = $1",
        str(action_id),
    )
    assert dict(row) == {
        "state": "sent",
        "provider_ref": "78",
        "route_ref": _RECIPIENT,
        "subject_state": "delivered",
    }
    stores = await pool.fetch(
        "SELECT source_store FROM public.owner_delivery_source_map ORDER BY source_store"
    )
    assert [r["source_store"] for r in stores] == [
        "approval_action",
        "rfc0023_intent",
        "rfc0023_presentation",
    ]
    won = await _redeem(pool, action_id, tokens[0], surface="telegram")
    assert won.outcome == "won"


async def test_ambiguous_provider_result_stays_unknown_and_claims_no_delivery(pool) -> None:
    await _enable(pool)
    action_id = await _park(pool)
    await _deliver(pool, HandoffResult("ambiguous", "provider_outcome_unknown"))

    row = await pool.fetchrow(
        "SELECT p.state, s.state AS subject_state FROM public.owner_delivery_presentations p "
        "JOIN public.owner_delivery_subjects s ON s.id = p.subject_id WHERE s.source_ref = $1",
        str(action_id),
    )
    assert dict(row) == {"state": "unknown", "subject_state": "pending"}
    kinds = {r["kind"] for r in await pool.fetch("SELECT kind FROM public.owner_delivery_receipts")}
    assert "send_unknown" in kinds and "sent" not in kinds


async def test_cutover_backfill_maps_old_cards_and_reaches_parity(pool) -> None:
    action_id = await _park(pool)
    await _deliver(pool, HandoffResult("confirmed", provider_reference="91"))
    async with pool.acquire() as conn, conn.transaction():
        # Before the backfill, parity names the delivered card with no subject.
        assert (await parity(conn)).mismatches == ((str(action_id), "presentation_unmapped"),)
        report = await bridge_existing(conn, owner_recipient=_RECIPIENT)
        again = await bridge_existing(conn, owner_recipient=_RECIPIENT)
    assert (report.subjects_opened, report.presentations_bridged) == (1, 1)
    assert (again.subjects_opened, again.presentations_bridged, again.mapped) == (0, 0, 0)
    row = await pool.fetchrow(
        "SELECT p.state, p.provider_ref, p.legacy_ref IS NOT NULL AS mapped, s.state AS subject "
        "FROM public.owner_delivery_presentations p "
        "JOIN public.owner_delivery_subjects s ON s.id = p.subject_id WHERE s.source_ref = $1",
        str(action_id),
    )
    assert dict(row) == {
        "state": "sent",
        "provider_ref": "91",
        "mapped": True,
        "subject": "delivered",
    }
    async with pool.acquire() as conn:
        assert (await parity(conn)).ok
    # The legacy card's decision still closes the backfilled subject: parity holds.
    await transition_pending_action(
        pool,
        action_id=action_id,
        target_status=ActionStatus.APPROVED,
        decided_by="human:owner@telegram",
        event_actor="human:owner@telegram",
        event_reason="legacy callback",
    )
    async with pool.acquire() as conn:
        report_after = await parity(conn)
        assert report_after.ok and report_after.checked == 1
        assert len(await od.pending_retractions(conn, surface="telegram")) == 1


def _app(pool: asyncpg.Pool) -> FastAPI:
    app = FastAPI()
    app.state.approval_callback_connector_token = _CONNECTOR_TOKEN
    app.state.owner_auth_service = _DomainOwnerState(_DASHBOARD_API_KEY)
    app.add_middleware(OwnerAuthMiddleware, config=OwnerAuthConfig.from_env(_DASHBOARD_API_KEY))
    app.include_router(owner_delivery_router.router)
    app.include_router(approvals_router.router)
    app.dependency_overrides[get_db] = lambda: db_manager(
        {"relationship": pool}, modules={"relationship": ["approvals"]}
    )
    app.dependency_overrides[approvals_router.get_mcp_manager] = MagicMock
    return app


async def test_route_decides_once_and_lists_the_stale_card_for_the_connector(pool) -> None:
    await _enable(pool)
    action_id = await _park(pool)
    _, cards = await _present(pool, action_id, "telegram")
    headers = {
        APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: _CONNECTOR_TOKEN,
        "X-Butlers-Decision-Actor": "owner@telegram",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(pool)), base_url="http://dashboard-api:41200"
    ) as client:
        first = await client.post(
            "/api/owner-delivery/actions",
            json={"token": cards["telegram"].tokens["reject"]},
            headers=headers,
        )
        replay = await client.post(
            "/api/owner-delivery/actions",
            json={"token": cards["telegram"].tokens["reject"]},
            headers=headers,
        )
        competing = await client.post(
            "/api/owner-delivery/actions",
            json={"token": cards["telegram"].tokens["approve"]},
            headers=headers,
        )
        unauthenticated = await client.get("/api/owner-delivery/retractions")
        listed = await client.get(
            "/api/owner-delivery/retractions",
            headers={APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: _CONNECTOR_TOKEN},
        )
        report = await client.post(
            f"/api/owner-delivery/retractions/{cards['telegram'].id}",
            json={"retracted": True},
            headers={APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: _CONNECTOR_TOKEN},
        )

    assert first.status_code == 200 and first.json()["data"]["status"] == "rejected"
    assert replay.status_code == 200 and replay.json()["data"]["status"] == "rejected"
    assert competing.status_code == 409
    assert competing.json()["detail"] == {
        "error": "owner_delivery_refused",
        "reason": "subject_resolved",
    }
    assert unauthenticated.status_code in (401, 403)
    assert [item["provider_ref"] for item in listed.json()["data"]] == ["55"]
    assert report.status_code == 200
    audit = await pool.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE target = $1 AND action = 'approval.deny'",
        str(action_id),
    )
    assert audit == 1


async def test_dashboard_dossier_is_a_presentation_with_its_own_tokens(pool) -> None:
    action_id = await _park(pool)
    assert await approvals_router._dashboard_owner_delivery_card(pool, action_id) is None
    assert await pool.fetchval("SELECT count(*) FROM public.owner_delivery_subjects") == 0

    await _enable(pool)
    _, cards = await _present(pool, action_id, "telegram")
    card = await approvals_router._dashboard_owner_delivery_card(pool, action_id)

    assert card is not None and card.generation == 1
    assert card.approve_token.startswith("od1:") and card.reject_token.startswith("od1:")
    # Telegram (sent) and the dashboard are presentations of the same subject.
    surfaces = await pool.fetch(
        "SELECT surface, state FROM public.owner_delivery_presentations ORDER BY surface"
    )
    assert [(r["surface"], r["state"]) for r in surfaces] == [
        ("dashboard", "sent"),
        ("telegram", "sent"),
    ]
    won = await _redeem(pool, action_id, card.reject_token, surface="dashboard")
    assert won.outcome == "won"
    stale = await _redeem(pool, action_id, cards["telegram"].tokens["approve"], surface="telegram")
    assert (stale.outcome, stale.reason) == ("refused", "subject_resolved")
    assert [t.provider_ref for t in won.retract] == ["55"]
    assert await approvals_router._dashboard_owner_delivery_card(pool, action_id) is None
