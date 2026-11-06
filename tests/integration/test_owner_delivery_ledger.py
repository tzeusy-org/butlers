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
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
from butlers.db import register_jsonb_codec
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
    owner_presentation_for,
    parity,
)
from butlers.modules.approvals.park import park_pending_action
from butlers.modules.approvals.previews import dashboard_call_preview
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
async def ledger_pool(db) -> asyncpg.Pool:
    """A kit-migrated core+approvals database with the delivery seams opened."""
    ledger_pool = await db("core", "approvals", max_size=8)
    await clear_audit_log_for_test(ledger_pool)
    await ledger_pool.execute(
        "UPDATE public.approvals_policy SET quiet_start_hour = NULL, "
        "quiet_end_hour = NULL, timezone = 'UTC' WHERE id = 1"
    )
    await ledger_pool.execute(
        "UPDATE approval_delivery_rollout SET admission_enabled = true, worker_enabled = false"
    )
    if (
        await ledger_pool.fetchval(
            "SELECT count(*) FROM public.entities WHERE 'owner' = ANY(roles)"
        )
        == 0
    ):
        await ledger_pool.execute(
            "INSERT INTO public.entities (canonical_name, entity_type, roles) "
            "VALUES ('Synthetic owner', 'person', ARRAY['owner'])"
        )
    return ledger_pool


async def _enable(ledger_pool: asyncpg.Pool, enabled: bool = True) -> None:
    await ledger_pool.execute("UPDATE public.owner_delivery_rollout SET enabled = $1", enabled)


async def _owner(ledger_pool: asyncpg.Pool) -> uuid.UUID:
    async with ledger_pool.acquire() as conn:
        owner = await od.owner_entity_id(conn)
    assert owner is not None
    return owner


async def _park(ledger_pool: asyncpg.Pool, ordinal: int = 0, *, card: bool = False) -> uuid.UUID:
    """Park one action. ``card=True`` uses a tool whose card shows the exact call.

    Approve and Reject are offered only on such a card; every other card is Open only
    and carries no token at all, whichever path renders it.
    """
    now = datetime.now(UTC) + timedelta(microseconds=ordinal)
    admission = await park_pending_action(
        ledger_pool,
        action_id=uuid.uuid4(),
        tool_name="telegram_send_message" if card else "relationship_assert_fact",
        tool_args=(
            {"chat_id": "synthetic", "text": f"Synthetic {ordinal}"}
            if card
            else {"ordinal": ordinal}
        ),
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
    ledger_pool: asyncpg.Pool, action_id: uuid.UUID, *surfaces: str, sent: bool = True
) -> tuple[od.Subject, dict[str, od.Presentation]]:
    """Open the subject and present it, marking provider surfaces as delivered."""
    out: dict[str, od.Presentation] = {}
    async with ledger_pool.acquire() as conn, conn.transaction():
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
    ledger_pool: asyncpg.Pool,
    action_id: uuid.UUID,
    token: str,
    *,
    surface: str,
    owner: uuid.UUID | None = None,
    now: datetime | None = None,
    digest: str | None = None,
    calls: list[str] | None = None,
    expect: str | None = None,
    before_decide: Any = None,
) -> od.Redemption:
    """Redeem inside the authority transaction, deciding through the real approvals ops.

    ``expect`` is the action the caller's route stands for; by default the token's own
    (the routes derive it the same way before redeeming). ``before_decide`` is awaited
    while the action and subject locks are held, to interleave a competing transaction.
    """

    async def decide(conn: Any, verb: str) -> dict[str, Any]:
        if calls is not None:
            calls.append(verb)
        if before_decide is not None:
            await before_decide()
        op = approvals_ops.approve_action if verb == "approve" else approvals_ops.reject_action
        result = await op(conn, action_id=str(action_id), actor_id="dashboard:rest-api")
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    async with ledger_pool.acquire() as conn, conn.transaction():
        action = await conn.fetchrow(
            "SELECT * FROM pending_actions WHERE id = $1 FOR UPDATE", action_id
        )
        _, current = approval_digests(action["tool_name"], action["tool_args"])
        verifier = od.token_verifier(token)
        expected = expect or await conn.fetchval(
            "SELECT action FROM public.owner_delivery_tokens WHERE verifier = $1", verifier
        )
        return await od.redeem(
            conn,
            token,
            kind="approval",
            source_ref=str(action_id),
            expected_action=expected or "approve",
            owner_entity=owner or await od.owner_entity_id(conn),  # type: ignore[arg-type]
            surface=surface,  # type: ignore[arg-type]
            current_action_digest=digest or current,
            decide=decide,
            now=now,
        )


async def _state(ledger_pool: asyncpg.Pool, action_id: uuid.UUID) -> tuple[str, str, int]:
    row = await ledger_pool.fetchrow(
        "SELECT pa.status, s.state, s.generation FROM pending_actions pa "
        "JOIN public.owner_delivery_subjects s ON s.source_ref = pa.id::text WHERE pa.id = $1",
        action_id,
    )
    return row["status"], row["state"], row["generation"]


async def test_token_wins_once_and_every_competing_token_goes_stale(ledger_pool) -> None:
    action_id = await _park(ledger_pool)
    _, cards = await _present(ledger_pool, action_id, "telegram", "dashboard")
    calls: list[str] = []

    won = await _redeem(
        ledger_pool, action_id, cards["telegram"].tokens["approve"], surface="telegram", calls=calls
    )
    assert won.outcome == "won" and won.action == "approve"
    assert calls == ["approve"]
    assert await _state(ledger_pool, action_id) == ("approved", "decided", 2)

    # Duplicate callback: same answer, no second effect.
    again = await _redeem(
        ledger_pool, action_id, cards["telegram"].tokens["approve"], surface="telegram", calls=calls
    )
    assert again.outcome == "duplicate" and calls == ["approve"]

    # The other surface's buttons are stale, whichever action they carry.
    for verb in ("approve", "reject", "ack"):
        stale = await _redeem(
            ledger_pool,
            action_id,
            cards["dashboard"].tokens[verb],
            surface="dashboard",
            calls=calls,
        )
        assert (stale.outcome, stale.reason) == ("refused", "subject_resolved")
    assert calls == ["approve"]

    # The dashboard presentation retracts in the ledger; the sent Telegram card is the
    # retraction target, and stays listed until a retraction is receipted.
    async with ledger_pool.acquire() as conn:
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
    kinds = await ledger_pool.fetch(
        "SELECT kind FROM public.owner_delivery_receipts ORDER BY created_at, id"
    )
    assert [row["kind"] for row in kinds].count("decided") == 1
    assert {"retract_failed", "retracted"} <= {row["kind"] for row in kinds}
    # Only verifiers are stored; no token text exists anywhere in the ledger.
    dump = await ledger_pool.fetchval(
        "SELECT string_agg(t::text, ' ') FROM public.owner_delivery_tokens t"
    )
    assert cards["telegram"].tokens["approve"].split(":", 1)[1] not in dump


@pytest.mark.parametrize(
    "case",
    ["wrong_owner", "wrong_surface", "token_expired", "action_changed", "subject_mismatch"],
)
async def test_bound_tokens_refuse_without_changing_anything(ledger_pool, case: str) -> None:
    action_id = await _park(ledger_pool)
    other_id = await _park(ledger_pool, ordinal=1)
    _, cards = await _present(ledger_pool, action_id, "telegram")
    await _present(ledger_pool, other_id, "telegram")
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

    refused = await _redeem(ledger_pool, target, token, calls=calls, **kwargs)

    assert (refused.outcome, refused.reason) == ("refused", case)
    assert calls == []
    assert await _state(ledger_pool, action_id) == ("pending", "delivered", 1)
    assert await ledger_pool.fetchval(
        "SELECT consumed_at IS NULL FROM public.owner_delivery_tokens WHERE presentation_id = $1 "
        "AND action = 'approve'",
        cards["telegram"].id,
    )
    # The refusal still works afterwards with the right bindings.
    assert (await _redeem(ledger_pool, action_id, token, surface="telegram")).outcome == "won"


async def test_ack_is_not_approval_but_stales_the_presented_actions(ledger_pool) -> None:
    action_id = await _park(ledger_pool)
    _, cards = await _present(ledger_pool, action_id, "telegram", "dashboard")

    ack = await _redeem(
        ledger_pool, action_id, cards["dashboard"].tokens["ack"], surface="dashboard"
    )

    assert ack.outcome == "won"
    assert await _state(ledger_pool, action_id) == ("pending", "acknowledged", 2)
    stale = await _redeem(
        ledger_pool, action_id, cards["telegram"].tokens["approve"], surface="telegram"
    )
    assert (stale.outcome, stale.reason) == ("refused", "stale_generation")
    # A fresh presentation at the new generation decides normally.
    _, fresh = await _present(ledger_pool, action_id, "telegram")
    assert fresh["telegram"].generation == 2
    won = await _redeem(
        ledger_pool, action_id, fresh["telegram"].tokens["reject"], surface="telegram"
    )
    assert won.outcome == "won"
    assert await _state(ledger_pool, action_id) == ("rejected", "decided", 3)


async def test_concurrent_decisions_on_two_surfaces_produce_one_effect(ledger_pool) -> None:
    action_id = await _park(ledger_pool)
    _, cards = await _present(ledger_pool, action_id, "telegram", "dashboard")
    calls: list[str] = []

    results = await asyncio.gather(
        _redeem(
            ledger_pool,
            action_id,
            cards["telegram"].tokens["approve"],
            surface="telegram",
            calls=calls,
        ),
        _redeem(
            ledger_pool,
            action_id,
            cards["dashboard"].tokens["reject"],
            surface="dashboard",
            calls=calls,
        ),
    )

    assert sorted(r.outcome for r in results) == ["refused", "won"]
    assert len(calls) == 1
    status, state, generation = await _state(ledger_pool, action_id)
    assert state == "decided" and generation == 2
    assert status == ("approved" if calls == ["approve"] else "rejected")


async def test_legacy_decision_closes_the_subject_and_stales_every_token(ledger_pool) -> None:
    """A decision outside a token (apr1, dashboard, expiry) still fences the cards."""
    action_id = await _park(ledger_pool)
    _, cards = await _present(ledger_pool, action_id, "telegram")

    transition = await transition_pending_action(
        ledger_pool,
        action_id=action_id,
        target_status=ActionStatus.REJECTED,
        decided_by="human:owner@telegram",
        event_actor="human:owner@telegram",
        event_reason="legacy callback",
    )

    assert transition.changed
    assert await _state(ledger_pool, action_id) == ("rejected", "decided", 2)
    refused = await _redeem(
        ledger_pool, action_id, cards["telegram"].tokens["approve"], surface="telegram"
    )
    assert (refused.outcome, refused.reason) == ("refused", "subject_resolved")
    async with ledger_pool.acquire() as conn:
        assert [
            t.presentation_id for t in await od.pending_retractions(conn, surface="telegram")
        ] == [cards["telegram"].id]


async def test_ledger_invariants_hold_for_the_table_owner_too(ledger_pool) -> None:
    action_id = await _park(ledger_pool)
    subject, cards = await _present(ledger_pool, action_id, "telegram")
    denied = asyncpg.InsufficientPrivilegeError
    with pytest.raises(denied):
        await ledger_pool.execute(
            "UPDATE public.owner_delivery_subjects SET action_digest = $2 WHERE id = $1",
            subject.id,
            "1" * 64,
        )
    with pytest.raises(denied):
        await ledger_pool.execute(
            "DELETE FROM public.owner_delivery_subjects WHERE id = $1", subject.id
        )
    with pytest.raises(denied):
        await ledger_pool.execute("UPDATE public.owner_delivery_receipts SET kind = 'sent'")
    with pytest.raises(denied):
        await ledger_pool.execute(
            "UPDATE public.owner_delivery_subjects SET generation = 0 WHERE id = $1", subject.id
        )
    # Tokens cannot be minted for an old generation.
    with pytest.raises(denied):
        await ledger_pool.execute(
            "INSERT INTO public.owner_delivery_tokens (verifier, subject_id, owning_schema, "
            "presentation_id, action, action_digest, owner_entity, surface, generation, "
            "expires_at) SELECT $1, id, owning_schema, $2, 'approve', action_digest, "
            "owner_entity, 'telegram', generation + 1, now() + interval '1 hour' "
            "FROM public.owner_delivery_subjects WHERE id = $3",
            "2" * 64,
            cards["telegram"].id,
            subject.id,
        )
    await _redeem(ledger_pool, action_id, cards["telegram"].tokens["approve"], surface="telegram")
    with pytest.raises(denied):  # resolved subjects are frozen
        await ledger_pool.execute(
            "UPDATE public.owner_delivery_subjects SET state = 'cancelled', decision = NULL "
            "WHERE id = $1",
            subject.id,
        )
    with pytest.raises(denied):  # consumed once
        await ledger_pool.execute(
            "UPDATE public.owner_delivery_tokens SET consumed_at = now(), outcome = '{}' "
            "WHERE subject_id = $1 AND consumed_at IS NOT NULL",
            subject.id,
        )
    for table in ("subjects", "presentations", "tokens", "receipts", "source_map", "rollout"):
        assert await ledger_pool.fetchval(
            "SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass",
            f"public.owner_delivery_{table}",
        )


async def test_failed_send_revokes_its_tokens_before_presenting_again(ledger_pool) -> None:
    action_id = await _park(ledger_pool)
    _, cards = await _present(ledger_pool, action_id, "telegram", sent=False)
    first = cards["telegram"]
    async with ledger_pool.acquire() as conn, conn.transaction():
        assert await od.begin_send(conn, first.id)
        await od.record_send(conn, first.id, outcome="failed")
    _, again = await _present(ledger_pool, action_id, "telegram", sent=False)

    assert again["telegram"].id == first.id and again["telegram"].tokens
    revoked = await _redeem(ledger_pool, action_id, first.tokens["approve"], surface="telegram")
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


async def _deliver(ledger_pool: asyncpg.Pool, result: HandoffResult) -> _Runtime:
    runtime = _Runtime(result)
    worker = ApprovalDeliveryWorker(
        ApprovalDeliveryRepository(ledger_pool), ApprovalDeliveryRenderer(), runtime
    )
    assert await worker.process_one() is True
    return runtime


async def test_worker_sends_canonical_tokens_only_when_enabled(ledger_pool) -> None:
    await _park(ledger_pool, card=True)
    runtime = await _deliver(ledger_pool, HandoffResult("confirmed", provider_reference="77"))
    legacy = _card_tokens(runtime.envelopes[0])
    assert len(legacy) == 2 and all(t.startswith("apr1:") for t in legacy)
    assert await ledger_pool.fetchval("SELECT count(*) FROM public.owner_delivery_subjects") == 0

    await _enable(ledger_pool)
    action_id = await _park(ledger_pool, ordinal=1, card=True)
    runtime = await _deliver(ledger_pool, HandoffResult("confirmed", provider_reference="78"))

    tokens = _card_tokens(runtime.envelopes[0])
    assert len(tokens) == 2 and all(t.startswith("od1:") for t in tokens)
    row = await ledger_pool.fetchrow(
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
    stores = await ledger_pool.fetch(
        "SELECT source_store FROM public.owner_delivery_source_map ORDER BY source_store"
    )
    assert [r["source_store"] for r in stores] == [
        "approval_action",
        "rfc0023_intent",
        "rfc0023_presentation",
    ]
    won = await _redeem(ledger_pool, action_id, tokens[0], surface="telegram")
    assert won.outcome == "won"


async def test_ambiguous_provider_result_stays_unknown_and_claims_no_delivery(ledger_pool) -> None:
    await _enable(ledger_pool)
    action_id = await _park(ledger_pool)
    await _deliver(ledger_pool, HandoffResult("ambiguous", "provider_outcome_unknown"))

    row = await ledger_pool.fetchrow(
        "SELECT p.state, s.state AS subject_state FROM public.owner_delivery_presentations p "
        "JOIN public.owner_delivery_subjects s ON s.id = p.subject_id WHERE s.source_ref = $1",
        str(action_id),
    )
    assert dict(row) == {"state": "unknown", "subject_state": "pending"}
    kinds = {
        r["kind"]
        for r in await ledger_pool.fetch("SELECT kind FROM public.owner_delivery_receipts")
    }
    assert "send_unknown" in kinds and "sent" not in kinds


async def test_cutover_backfill_maps_old_cards_and_reaches_parity(ledger_pool) -> None:
    action_id = await _park(ledger_pool)
    await _deliver(ledger_pool, HandoffResult("confirmed", provider_reference="91"))
    async with ledger_pool.acquire() as conn, conn.transaction():
        # Before the backfill, parity names the delivered card with no subject.
        assert (await parity(conn)).mismatches == ((str(action_id), "presentation_unmapped"),)
        report = await bridge_existing(conn, owner_recipient=_RECIPIENT)
        again = await bridge_existing(conn, owner_recipient=_RECIPIENT)
    assert (report.subjects_opened, report.presentations_bridged) == (1, 1)
    assert (again.subjects_opened, again.presentations_bridged, again.mapped) == (0, 0, 0)
    row = await ledger_pool.fetchrow(
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
    async with ledger_pool.acquire() as conn:
        assert (await parity(conn)).ok
    # The legacy card's decision still closes the backfilled subject: parity holds.
    await transition_pending_action(
        ledger_pool,
        action_id=action_id,
        target_status=ActionStatus.APPROVED,
        decided_by="human:owner@telegram",
        event_actor="human:owner@telegram",
        event_reason="legacy callback",
    )
    async with ledger_pool.acquire() as conn:
        report_after = await parity(conn)
        assert report_after.ok and report_after.checked == 1
        assert len(await od.pending_retractions(conn, surface="telegram")) == 1


def _app(ledger_pool: asyncpg.Pool) -> FastAPI:
    app = FastAPI()
    app.state.approval_callback_connector_token = _CONNECTOR_TOKEN
    app.state.owner_auth_service = _DomainOwnerState(_DASHBOARD_API_KEY)
    app.add_middleware(OwnerAuthMiddleware, config=OwnerAuthConfig.from_env(_DASHBOARD_API_KEY))
    app.include_router(owner_delivery_router.router)
    app.include_router(approvals_router.router)
    app.dependency_overrides[get_db] = lambda: db_manager(
        {"relationship": ledger_pool}, modules={"relationship": ["approvals"]}
    )
    # A lambda: FastAPI reads the MagicMock class's (*args, **kw) as required query params.
    app.dependency_overrides[approvals_router.get_mcp_manager] = lambda: MagicMock()
    return app


async def test_route_decides_once_and_lists_the_stale_card_for_the_connector(ledger_pool) -> None:
    await _enable(ledger_pool)
    action_id = await _park(ledger_pool)
    _, cards = await _present(ledger_pool, action_id, "telegram")
    headers = {
        APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: _CONNECTOR_TOKEN,
        "X-Butlers-Decision-Actor": "owner@telegram",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(ledger_pool)), base_url="http://dashboard-api:41200"
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
    audit = await ledger_pool.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE target = $1 AND action = 'approval.deny'",
        str(action_id),
    )
    assert audit == 1


async def test_dashboard_dossier_is_a_presentation_with_its_own_tokens(ledger_pool) -> None:
    action_id = await _park(ledger_pool)
    assert await approvals_router._dashboard_owner_delivery_card(ledger_pool, action_id) is None
    assert await ledger_pool.fetchval("SELECT count(*) FROM public.owner_delivery_subjects") == 0

    await _enable(ledger_pool)
    _, cards = await _present(ledger_pool, action_id, "telegram")
    card = await approvals_router._dashboard_owner_delivery_card(ledger_pool, action_id)

    assert card is not None and card.generation == 1
    assert card.approve_token.startswith("od1:") and card.reject_token.startswith("od1:")
    # Telegram (sent) and the dashboard are presentations of the same subject.
    surfaces = await ledger_pool.fetch(
        "SELECT surface, state FROM public.owner_delivery_presentations ORDER BY surface"
    )
    assert [(r["surface"], r["state"]) for r in surfaces] == [
        ("dashboard", "sent"),
        ("telegram", "sent"),
    ]
    won = await _redeem(ledger_pool, action_id, card.reject_token, surface="dashboard")
    assert won.outcome == "won"
    stale = await _redeem(
        ledger_pool, action_id, cards["telegram"].tokens["approve"], surface="telegram"
    )
    assert (stale.outcome, stale.reason) == ("refused", "subject_resolved")
    assert [t.provider_ref for t in won.retract] == ["55"]
    assert await approvals_router._dashboard_owner_delivery_card(ledger_pool, action_id) is None


async def test_ack_then_expiry_or_cancellation_still_closes_the_subject(ledger_pool) -> None:
    """An acknowledgement's surface must not block a later expiry or cancellation."""
    for target in (ActionStatus.EXPIRED, ActionStatus.CANCELLED):
        action_id = await _park(ledger_pool, ordinal=int(target is ActionStatus.CANCELLED))
        _, cards = await _present(ledger_pool, action_id, "telegram", "dashboard")
        ack = await _redeem(
            ledger_pool, action_id, cards["telegram"].tokens["ack"], surface="telegram"
        )
        assert ack.outcome == "won"

        transition = await transition_pending_action(
            ledger_pool,
            action_id=action_id,
            target_status=target,
            decided_by="system:expiry",
            event_actor="system:expiry",
            event_reason="synthetic close after ack",
        )

        assert transition.changed
        row = await ledger_pool.fetchrow(
            "SELECT pa.status, s.state, s.decided_surface, s.generation "
            "FROM pending_actions pa JOIN public.owner_delivery_subjects s "
            "ON s.source_ref = pa.id::text WHERE pa.id = $1",
            action_id,
        )
        assert dict(row) == {
            "status": target.value,
            "state": target.value,
            "decided_surface": None,
            "generation": 3,
        }


async def test_an_ack_token_never_reaches_approve_or_deny(ledger_pool) -> None:
    action_id = await _park(ledger_pool)
    _, cards = await _present(ledger_pool, action_id, "telegram")
    calls: list[str] = []
    for route_action in ("approve", "reject"):
        refused = await _redeem(
            ledger_pool,
            action_id,
            cards["telegram"].tokens["ack"],
            surface="telegram",
            expect=route_action,
            calls=calls,
        )
        assert (refused.outcome, refused.reason) == ("refused", "action_mismatch")
    assert calls == []
    assert await _state(ledger_pool, action_id) == ("pending", "delivered", 1)


async def test_no_owner_delivery_table_can_be_truncated(ledger_pool) -> None:
    for table in ("subjects", "presentations", "tokens", "receipts", "source_map"):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await ledger_pool.execute(f"TRUNCATE public.owner_delivery_{table} CASCADE")


async def test_a_live_card_cannot_be_marked_retracted(ledger_pool) -> None:
    action_id = await _park(ledger_pool)
    _, cards = await _present(ledger_pool, action_id, "telegram")
    async with ledger_pool.acquire() as conn:
        with pytest.raises(od.OwnerDeliveryRefused, match="presentation_live"):
            async with conn.transaction():
                await od.record_retraction(conn, cards["telegram"].id, retracted=True)
    assert (
        await ledger_pool.fetchval(
            "SELECT state FROM public.owner_delivery_presentations WHERE id = $1",
            cards["telegram"].id,
        )
        == "sent"
    )


async def test_receipts_cannot_name_another_subjects_presentation(ledger_pool) -> None:
    first = await _park(ledger_pool)
    second = await _park(ledger_pool, ordinal=1)
    subject, _ = await _present(ledger_pool, first, "telegram")
    _, other = await _present(ledger_pool, second, "telegram")
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await ledger_pool.execute(
            "INSERT INTO public.owner_delivery_receipts "
            "(subject_id, owning_schema, presentation_id, kind, generation) "
            "SELECT id, owning_schema, $2, 'sent', 1 FROM public.owner_delivery_subjects "
            "WHERE id = $1",
            subject.id,
            other["telegram"].id,
        )
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await ledger_pool.execute(
            "INSERT INTO public.owner_delivery_source_map "
            "(owning_schema, source_store, source_id, subject_id, presentation_id) "
            "SELECT owning_schema, 'attention', 'x', id, $2 FROM public.owner_delivery_subjects "
            "WHERE id = $1",
            subject.id,
            other["telegram"].id,
        )


async def test_settling_a_send_and_redeeming_concurrently_never_deadlock(ledger_pool) -> None:
    """record_send and redeem both lock the subject first, so they queue."""
    for ordinal in range(8):
        action_id = await _park(ledger_pool, ordinal=ordinal)
        _, cards = await _present(ledger_pool, action_id, "telegram", "dashboard", sent=False)
        async with ledger_pool.acquire() as conn, conn.transaction():
            assert await od.begin_send(conn, cards["telegram"].id)

        async def settle(presentation_id: uuid.UUID = cards["telegram"].id) -> None:
            async with ledger_pool.acquire() as conn, conn.transaction():
                await od.record_send(conn, presentation_id, outcome="sent", provider_ref="60")

        results = await asyncio.gather(
            settle(),
            _redeem(
                ledger_pool, action_id, cards["dashboard"].tokens["reject"], surface="dashboard"
            ),
            return_exceptions=True,
        )
        assert not any(isinstance(r, BaseException) for r in results), results
        assert results[1].outcome == "won"


class _HeldHandoff(_Runtime):
    """A provider call that reports in only when the test releases it."""

    def __init__(self, result: HandoffResult) -> None:
        super().__init__(result)
        self.handed = asyncio.Event()
        self.release = asyncio.Event()

    async def handoff(self, claim: DeliveryClaim, envelope: dict[str, Any]) -> HandoffResult:
        self.envelopes.append(envelope)
        self.handed.set()
        await self.release.wait()
        return self.result


async def test_handoff_result_does_not_wait_for_a_held_action_row(ledger_pool) -> None:
    """The executor holds the action row across the tool call; a result must not queue on it."""
    action_id = await _park(ledger_pool)
    runtime = _HeldHandoff(HandoffResult("confirmed", provider_reference="82"))
    worker = ApprovalDeliveryWorker(
        ApprovalDeliveryRepository(ledger_pool), ApprovalDeliveryRenderer(), runtime
    )
    delivering = asyncio.create_task(worker.process_one())
    await asyncio.wait_for(runtime.handed.wait(), 10)
    async with ledger_pool.acquire() as executor, executor.transaction():
        await executor.execute("SELECT 1 FROM pending_actions WHERE id = $1 FOR UPDATE", action_id)
        runtime.release.set()
        assert await asyncio.wait_for(delivering, 10) is True


async def test_a_schema_without_the_ledger_is_simply_off(db) -> None:
    approvals_only = await db("approvals")
    async with approvals_only.acquire() as conn:
        assert await od.rollout_enabled(conn) is False
        assert await owner_presentation_for(conn, uuid.uuid4(), 1) is None


async def test_worker_still_delivers_legacy_cards_on_a_schema_without_the_ledger(db) -> None:
    """No ledger reads as off: the card is handed off, not retried under a preflight reason."""
    approvals_only = await db("approvals")
    now = datetime.now(UTC)
    action_id, intent_id = uuid.uuid4(), uuid.uuid4()
    schema = await approvals_only.fetchval("SELECT current_schema()")
    action_key = f"approval:{schema}:{action_id}"
    await approvals_only.execute(
        "INSERT INTO pending_actions (id, tool_name, tool_args, agent_summary, status, "
        "requested_at, expires_at, why, evidence, blast_radius, reversibility) "
        "VALUES ($1, 'telegram_send_message', $2, 'Synthetic', 'pending', $3, $4, "
        "'Synthetic reason', '[]'::jsonb, 'contact', 'compensable')",
        action_id,
        {"chat_id": "synthetic", "text": "Synthetic"},
        now,
        now + timedelta(days=3),
    )
    await approvals_only.execute(
        "INSERT INTO approval_delivery_intents "
        "(id, action_id, action_key, owning_schema, origin_butler, admission_mode) "
        "VALUES ($1, $2, $3, $4, 'relationship', 'single')",
        intent_id,
        action_id,
        action_key,
        schema,
    )
    await approvals_only.execute(
        "INSERT INTO approval_delivery_presentations (intent_id, subject_key, subject_kind, "
        "presentation_mode, presentation_generation, presentation_key, state, not_before, "
        "next_attempt_at) VALUES ($1, $2, 'action', 'single', 1, $2 || ':p:1', 'ready', $3, $3)",
        intent_id,
        action_key,
        now,
    )

    runtime = await _deliver(approvals_only, HandoffResult("confirmed", provider_reference="83"))

    tokens = _card_tokens(runtime.envelopes[0])
    assert len(tokens) == 2 and all(t.startswith("apr1:") for t in tokens)
    row = await approvals_only.fetchrow(
        "SELECT state, last_reason_code FROM approval_delivery_presentations WHERE intent_id = $1",
        intent_id,
    )
    assert dict(row) == {"state": "delivered", "last_reason_code": None}


async def test_handoff_result_and_token_reject_on_one_card_never_deadlock(ledger_pool) -> None:
    """complete_handoff takes the owner subject first, as decisions do (LOCK_ORDER).

    The reject holds the action and subject, then (in transition_pending_action) needs
    the RFC 0023 presentation. The handoff result is released inside that window: a
    presentation-first complete_handoff would hold the RFC 0023 row and wait for the
    subject, a cycle PostgreSQL aborts as a deadlock.
    """
    await _enable(ledger_pool)
    action_id = await _park(ledger_pool, card=True)
    runtime = _HeldHandoff(HandoffResult("confirmed", provider_reference="81"))
    worker = ApprovalDeliveryWorker(
        ApprovalDeliveryRepository(ledger_pool), ApprovalDeliveryRenderer(), runtime
    )
    delivering = asyncio.create_task(worker.process_one())
    await asyncio.wait_for(runtime.handed.wait(), 10)
    rfc_state = await ledger_pool.fetchval(
        "SELECT p.state FROM approval_delivery_presentations p "
        "JOIN approval_delivery_intents i ON i.id = p.intent_id WHERE i.action_id = $1",
        action_id,
    )
    assert rfc_state == "handoff_started"
    reject = next(
        a["callback_token"]
        for a in runtime.envelopes[0]["actions"]
        if a.get("callback_token", "").startswith("od1:") and a.get("verb") == "reject"
    )

    async def release_the_result_mid_decision() -> None:
        runtime.release.set()
        await asyncio.sleep(0.5)  # complete_handoff now runs and blocks on a lock

    results = await asyncio.wait_for(
        asyncio.gather(
            _redeem(
                ledger_pool,
                action_id,
                reject,
                surface="telegram",
                expect="reject",
                before_decide=release_the_result_mid_decision,
            ),
            delivering,
            return_exceptions=True,
        ),
        20,
    )
    assert not any(isinstance(r, BaseException) for r in results), results
    assert results[0].outcome == "won" and results[1] is True
    assert await _state(ledger_pool, action_id) == ("rejected", "decided", 2)
    settled = await ledger_pool.fetchrow(
        "SELECT p.state AS rfc_state, a.outcome FROM approval_delivery_presentations p "
        "JOIN approval_delivery_intents i ON i.id = p.intent_id "
        "JOIN approval_delivery_attempts a ON a.presentation_id = p.id "
        "AND a.outcome <> 'started' WHERE i.action_id = $1",
        action_id,
    )
    # The decision cancelled the in-flight card; the late result is still receipted.
    assert dict(settled) == {"rfc_state": "cancelled", "outcome": "confirmed"}
    card = await ledger_pool.fetchrow(
        "SELECT p.state, p.provider_ref FROM public.owner_delivery_presentations p "
        "JOIN public.owner_delivery_subjects s ON s.id = p.subject_id WHERE s.source_ref = $1",
        str(action_id),
    )
    assert dict(card) == {"state": "sent", "provider_ref": "81"}


async def _stale_telegram_card(ledger_pool: asyncpg.Pool) -> uuid.UUID:
    action_id = await _park(ledger_pool)
    _, cards = await _present(ledger_pool, action_id, "telegram")
    await transition_pending_action(
        ledger_pool,
        action_id=action_id,
        target_status=ActionStatus.CANCELLED,
        decided_by="system:test",
        event_actor="system:test",
        event_reason="synthetic",
    )
    return cards["telegram"].id


async def _listed(ledger_pool: asyncpg.Pool) -> list[uuid.UUID]:
    async with ledger_pool.acquire() as conn:
        return [t.presentation_id for t in await od.pending_retractions(conn, surface="telegram")]


async def _fail_retraction(ledger_pool: asyncpg.Pool, card: uuid.UUID) -> None:
    async with ledger_pool.acquire() as conn, conn.transaction():
        await od.record_retraction(conn, card, retracted=False, reason="provider_error")


async def _age_receipts(owner: asyncpg.Pool, seconds: int) -> None:
    """Receipts are append-only by trigger, so age one the only way: disable it for the test."""
    trigger = "owner_delivery_receipts_append_only"
    await owner.execute(
        f"ALTER TABLE public.owner_delivery_receipts DISABLE TRIGGER {trigger}; "
        f"UPDATE public.owner_delivery_receipts SET created_at = "
        f"created_at - interval '{seconds} seconds'; "
        f"ALTER TABLE public.owner_delivery_receipts ENABLE TRIGGER {trigger}"
    )


async def test_a_card_staled_without_any_tap_is_listed_for_the_sweep(ledger_pool) -> None:
    card = await _stale_telegram_card(ledger_pool)

    assert await _listed(ledger_pool) == [card]

    async with ledger_pool.acquire() as conn, conn.transaction():
        await od.record_retraction(conn, card, retracted=True)
    assert await _listed(ledger_pool) == []


async def test_a_failed_retraction_cools_down_and_is_eventually_given_up(ledger_pool) -> None:
    """Every failure writes a receipt, so a periodic sweep must not retry a dead card forever."""
    card = await _stale_telegram_card(ledger_pool)

    await _fail_retraction(ledger_pool, card)
    assert await _listed(ledger_pool) == [], "listed again during the cooldown"

    await _age_receipts(ledger_pool, od.RETRACTION_RETRY_AFTER_S + 1)
    assert await _listed(ledger_pool) == [card], "a failed card gets another attempt"

    for _ in range(od.RETRACTION_MAX_FAILED_ATTEMPTS - 1):
        await _fail_retraction(ledger_pool, card)
    await _age_receipts(ledger_pool, od.RETRACTION_RETRY_AFTER_S + 1)
    assert await _listed(ledger_pool) == [], "still retried after the bound"
    # Honest: nothing claims the card was retracted, and every attempt has its receipt.
    assert (
        await ledger_pool.fetchval(
            "SELECT state FROM public.owner_delivery_presentations WHERE id = $1", card
        )
        == "sent"
    )
    assert (
        await ledger_pool.fetchval(
            "SELECT count(*) FROM public.owner_delivery_receipts "
            "WHERE presentation_id = $1 AND kind = 'retract_failed'",
            card,
        )
        == od.RETRACTION_MAX_FAILED_ATTEMPTS
    )


@pytest.mark.parametrize("verb", ["approve", "reject"])
async def test_dashboard_session_decides_through_its_token_and_stales_telegram(
    ledger_pool, monkeypatch, verb: str
) -> None:
    """The real route on a browser session: displayed call required, Telegram card listed."""
    monkeypatch.setattr(
        approvals_router,
        "_dispatch_approved_action_outcome",
        AsyncMock(return_value=SimpleNamespace(kind="failed", action=None)),
    )
    await _enable(ledger_pool)
    action_id = await _park(ledger_pool, card=True)
    _, cards = await _present(ledger_pool, action_id, "telegram")
    card = await approvals_router._dashboard_owner_delivery_card(ledger_pool, action_id)
    assert card is not None
    row = await ledger_pool.fetchrow(
        "SELECT tool_name, tool_args FROM pending_actions WHERE id = $1", action_id
    )
    shown = dashboard_call_preview(row["tool_name"], row["tool_args"])["displayed_call"]
    # No connector credential and no auth middleware: what a verified browser session reaches.
    app = FastAPI()
    app.include_router(owner_delivery_router.router)
    app.dependency_overrides[get_db] = lambda: db_manager(
        {"relationship": ledger_pool}, modules={"relationship": ["approvals"]}
    )
    app.dependency_overrides[approvals_router.get_mcp_manager] = lambda: MagicMock()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://dashboard-api:41200"
    ) as client:
        unshown = await client.post(
            "/api/owner-delivery/actions", json={"token": card.approve_token}
        )
        assert unshown.status_code == 409
        assert unshown.json()["detail"] == approvals_router.APPROVAL_CALL_CHANGED_DETAIL
        assert await _state(ledger_pool, action_id) == ("pending", "delivered", 1)

        decided_by_owner = await client.post(
            "/api/owner-delivery/actions",
            json=(
                {"token": card.approve_token, "displayed_call": shown}
                if verb == "approve"
                else {"token": card.reject_token, "reason": "Not this one"}
            ),
        )

    status = "approved" if verb == "approve" else "rejected"
    assert decided_by_owner.status_code == 200
    assert decided_by_owner.json()["data"]["status"] == status
    assert await _state(ledger_pool, action_id) == (status, "decided", 2)
    assert await _listed(ledger_pool) == [cards["telegram"].id]
    decided = await ledger_pool.fetchval(
        "SELECT decided_surface FROM public.owner_delivery_subjects WHERE source_ref = $1",
        str(action_id),
    )
    assert decided == "dashboard"


async def test_minted_tokens_stay_redeemable_after_the_flag_is_turned_off(ledger_pool) -> None:
    """The flag governs minting only: live cards are not stranded, the drain keeps going."""
    await _enable(ledger_pool)
    action_id = await _park(ledger_pool)
    _, cards = await _present(ledger_pool, action_id, "telegram", "dashboard")
    await _enable(ledger_pool, False)

    assert await approvals_router._dashboard_owner_delivery_card(ledger_pool, action_id) is None
    won = await _redeem(
        ledger_pool, action_id, cards["dashboard"].tokens["approve"], surface="dashboard"
    )
    assert won.outcome == "won"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(ledger_pool)), base_url="http://dashboard-api:41200"
    ) as client:
        listed = await client.get(
            "/api/owner-delivery/retractions",
            headers={APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: _CONNECTOR_TOKEN},
        )
    assert [item["presentation_id"] for item in listed.json()["data"]] == [
        str(cards["telegram"].id)
    ]


@pytest.fixture
async def role_pools(db, fresh_db) -> tuple[asyncpg.Pool, asyncpg.Pool]:
    """``(owner, runtime)`` pools on a general-schema database; runtime is the butler role."""
    # fresh_db migrates through asyncio.run, so it cannot run on this fixture's event loop.
    url = await asyncio.to_thread(
        fresh_db, "core", "approvals", schemas={"core": "general", "approvals": "general"}
    )
    owner = await db.open(url, schema="general")
    await owner.execute(
        "UPDATE public.approvals_policy SET quiet_start_hour = NULL, "
        "quiet_end_hour = NULL, timezone = 'UTC' WHERE id = 1"
    )
    if await owner.fetchval("SELECT count(*) FROM public.entities WHERE 'owner' = ANY(roles)") == 0:
        await owner.execute(
            "INSERT INTO public.entities (canonical_name, entity_type, roles) "
            "VALUES ('Synthetic owner', 'person', ARRAY['owner'])"
        )

    async def as_butler(connection: asyncpg.Connection) -> None:
        await register_jsonb_codec(connection)  # as every real butler pool does
        await connection.execute("SET ROLE butler_general_rw")

    runtime = await asyncpg.create_pool(
        url,
        min_size=1,
        max_size=3,
        init=as_butler,
        server_settings={"search_path": "general,public"},
    )
    try:
        assert await runtime.fetchval("SELECT current_user") == "butler_general_rw"
        assert not await runtime.fetchval(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        yield owner, runtime
    finally:
        await runtime.close()


async def _foreign_subject(owner: asyncpg.Pool) -> uuid.UUID:
    """A subject another schema owns, written through the owner's RLS bypass."""
    owner_entity = await owner.fetchval(
        "SELECT id FROM public.entities WHERE 'owner' = ANY(roles) LIMIT 1"
    )
    return await owner.fetchval(
        "INSERT INTO public.owner_delivery_subjects (id, kind, owning_schema, source_ref, "
        "owner_entity, content_digest, action_digest) "
        "VALUES (gen_random_uuid(), 'approval', 'finance', 'foreign-action', $1, $2, $2) "
        "RETURNING id",
        owner_entity,
        "a" * 64,
    )


async def test_runtime_role_cannot_see_or_write_another_schemas_rows(role_pools) -> None:
    owner, runtime = role_pools
    foreign = await _foreign_subject(owner)

    assert (
        await runtime.fetchval(
            "SELECT count(*) FROM public.owner_delivery_subjects WHERE id = $1", foreign
        )
        == 0
    )
    assert (
        await runtime.execute(
            "UPDATE public.owner_delivery_subjects SET state = 'cancelled', "
            "resolved_at = now() WHERE id = $1",
            foreign,
        )
        == "UPDATE 0"
    )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await runtime.execute(
            "INSERT INTO public.owner_delivery_subjects (id, kind, owning_schema, source_ref, "
            "owner_entity, content_digest, action_digest) SELECT gen_random_uuid(), "
            "'approval', 'finance', 'forged', owner_entity, content_digest, action_digest "
            "FROM (SELECT $1::uuid AS owner_entity, $2 AS content_digest, $2 AS action_digest) v",
            uuid.uuid4(),
            "b" * 64,
        )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await runtime.execute(
            "INSERT INTO public.owner_delivery_receipts (subject_id, owning_schema, kind, "
            "generation) VALUES ($1, 'finance', 'sent', 1)",
            foreign,
        )
    assert (
        await owner.fetchval(
            "SELECT state FROM public.owner_delivery_subjects WHERE id = $1", foreign
        )
        == "pending"
    )


async def test_runtime_role_cannot_flip_the_rollout_flag(role_pools) -> None:
    owner, runtime = role_pools
    assert await runtime.fetchval("SELECT enabled FROM public.owner_delivery_rollout") is False
    try:
        tag = await runtime.execute("UPDATE public.owner_delivery_rollout SET enabled = true")
    except asyncpg.InsufficientPrivilegeError:
        tag = "refused"
    assert tag in ("UPDATE 0", "refused")
    assert await owner.fetchval("SELECT enabled FROM public.owner_delivery_rollout") is False


async def test_runtime_role_redeems_its_own_schemas_card(role_pools) -> None:
    owner, runtime = role_pools
    action_id = await _park(runtime)
    _, cards = await _present(runtime, action_id, "telegram")

    won = await _redeem(runtime, action_id, cards["telegram"].tokens["reject"], surface="telegram")

    assert won.outcome == "won"
    assert await _state(owner, action_id) == ("rejected", "decided", 2)
    assert (
        await owner.fetchval(
            "SELECT owning_schema FROM public.owner_delivery_subjects WHERE source_ref = $1",
            str(action_id),
        )
        == "general"
    )
