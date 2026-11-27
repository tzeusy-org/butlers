"""Gmail's exact-call approval seam on migrated PostgreSQL and fake Gmail only."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import asyncpg
import httpx
import pytest
from fastmcp import FastMCP

from butlers.capability_broker.contracts import Catalog, Reason
from butlers.capability_broker.forwarder import HttpForwarder
from butlers.capability_broker.verification import AssertionVerificationMiddleware, DomainVerifier
from butlers.config import ApprovalConfig, ApprovalRiskTier, GatedToolConfig
from butlers.core import goal_lineage
from butlers.core.runtime_probe_control.keys import VerifierKey, VerifierKeyring
from butlers.core.tool_catalog import RecipientParameters
from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_BY_NAME
from butlers.mcp_wrappers import _SpanWrappingMCP
from butlers.modules.approvals.gate import apply_approval_gates
from butlers.modules.approvals.goal_park import decide_goal_effect
from butlers.modules.gmail import GmailModule
from tests.capability_broker.helpers import capability
from tests.capability_broker.test_broker_ledger import (
    OWNER as OWNER_ENTITY,
)
from tests.capability_broker.test_broker_ledger import (
    StaticCatalog,
    _grant,
    _refused,
    world,
)
from tests.modules.test_module_gmail import OWNER, FakeGmail, _account, decode_mime, reply_args

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

SEND = capability(
    "gmail_send_reply",
    "communicate",
    "messenger",
    idempotency="key_required",
    recipient_params=RecipientParameters(argument_names=("to", "cc")),
    safety_args=TOOL_PRESENTATION_BY_NAME["gmail_send_reply"].safety_args,
    categories=frozenset(),
)
DRAFT = capability("gmail_save_draft", "record", "messenger", categories=frozenset())
CATALOG = StaticCatalog(Catalog("catalog-v1", (SEND, DRAFT)))


@pytest.fixture(scope="module")
def gmail_db_url(broker_db_url):
    from butlers.migrations import run_migrations

    asyncio.run(run_migrations(broker_db_url, chain="messenger", schema="messenger"))
    return broker_db_url


@asynccontextmanager
async def gmail_domain(w, monkeypatch):
    """Real D7 verification, real Messenger gate/receipts, local ASGI and synthetic Gmail."""
    monkeypatch.setattr(goal_lineage, "_enforced", True)
    server = FastMCP("messenger")
    proxy = _SpanWrappingMCP(server, "messenger", module_name="gmail")
    module, fake = GmailModule(), FakeGmail()
    await module.register_tools(proxy, {}, SimpleNamespace(pool=w.domain), "messenger")
    module._http_client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    module._authorize = AsyncMock(return_value=(_account(OWNER), "tok-1"))
    await apply_approval_gates(
        server,
        ApprovalConfig(
            enabled=True,
            gated_tools={"gmail_send_reply": GatedToolConfig(risk_tier=ApprovalRiskTier.MEDIUM)},
        ),
        w.domain,
        "messenger",
        module.tool_metadata(),
    )
    keyring = VerifierKeyring(
        VerifierKey(
            w.signer.kid,
            w.signer.ed25519.public_key().public_bytes_raw(),
            datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    server.middleware.insert(
        0,
        AssertionVerificationMiddleware(
            DomainVerifier("messenger", keyring, started_at=int(time.time()) - 5)
        ),
    )
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)

    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), **kwargs)

    w.forwarder.via = HttpForwarder(
        {"messenger": "http://domain/mcp"}, httpx_client_factory=factory
    )
    try:
        with patch("butlers.mcp_wrappers.capture_tool_call"):
            async with app.lifespan(app):
                yield fake
    finally:
        await module.on_shutdown()


async def approve(w, goal, parked):
    decision = await decide_goal_effect(
        w.domain, goal, parked.effect_id, approve=True, decided_by="owner:dashboard"
    )
    assert decision.status == "approved"
    return await w.broker.execute_approved(
        goal, parked.effect_id, parked.approval_ref, decision.tool_args
    )


async def test_exact_stored_reply_sends_once_through_verified_domain(gmail_db_url, monkeypatch):
    async with world(gmail_db_url, catalog=CATALOG) as w, gmail_domain(w, monkeypatch) as fake:
        goal = await w.goal(_grant(SEND), exposed=True)
        invocation, token = await w.invocation(goal, _grant(SEND))
        args = reply_args()
        parked = await w.broker.call(goal, token, SEND.identity, args)
        assert parked.status == "parked" and fake.sent == []
        action = await w.action(parked.effect_id)
        assert action["tool_args"] == args
        assert (action["goal_id"], action["goal_invocation_id"], action["goal_exposed"]) == (
            goal,
            invocation,
            True,
        )
        done = await approve(w, goal, parked)
        assert done.status == "committed", done
        assert decode_mime(fake.sent[0]).get_content() == args["body"]
        assert len(fake.sent) == 1
        duplicate = await _refused(w.broker.call(goal, token, SEND.identity, args))
        assert duplicate.reason is Reason.DUPLICATE and len(fake.sent) == 1
        assert (await w.action(parked.effect_id))["status"] == "executed"


@pytest.mark.parametrize("times_out", [False, True])
async def test_fake_provider_failures_settle_real_effects_without_replay(
    gmail_db_url, monkeypatch, times_out
):
    async with world(gmail_db_url, catalog=CATALOG) as w, gmail_domain(w, monkeypatch) as fake:
        fake.send_status, fake.send_timeout = 400, times_out
        goal = await w.goal(_grant(SEND))
        _, token = await w.invocation(goal, _grant(SEND))
        args = reply_args()
        parked = await w.broker.call(goal, token, SEND.identity, args)
        result = await approve(w, goal, parked)
        expected = "unknown" if times_out else "failed"
        assert result.status == expected
        assert (await w.effect(parked.effect_id))["status"] == expected
        assert (
            await w.domain.fetchval(
                "SELECT state FROM gmail_owner_sends WHERE effect_id=$1", parked.effect_id
            )
            == expected
        )
        assert len(fake.sent) == 1
        if times_out:
            duplicate = await _refused(w.broker.call(goal, token, SEND.identity, args))
            assert duplicate.reason is Reason.DUPLICATE and len(fake.sent) == 1


@pytest.mark.parametrize(
    "origin,exposed,expected",
    [
        ("third_party", False, "parked"),
        ("owner", True, "parked"),
        ("owner", False, "committed"),
    ],
)
async def test_draft_parking_preserves_lineage_and_blocks_standing_rules(
    gmail_db_url, monkeypatch, origin, exposed, expected
):
    async with world(gmail_db_url, catalog=CATALOG) as w, gmail_domain(w, monkeypatch) as fake:
        args = {
            "account": OWNER,
            "to": ["ann@example.test"],
            "subject": "Draft",
            "body": "Only a draft",
        }
        await w.domain.execute(
            "INSERT INTO approval_rules (id, tool_name, arg_constraints, description) "
            "VALUES ($1, 'gmail_save_draft', $2::jsonb, 'exact draft control')",
            uuid4(),
            {key: {"type": "exact", "value": value} for key, value in args.items()},
        )
        goal = await w.goal(_grant(DRAFT), origin=origin, exposed=exposed)
        invocation, token = await w.invocation(goal, _grant(DRAFT))
        result = await w.broker.call(goal, token, DRAFT.identity, args)
        assert result.status == expected
        if expected == "parked":
            assert fake.drafts == {}
            action = await w.action(result.effect_id)
            assert action["goal_rule_possible"] is False
            assert (action["goal_id"], action["goal_invocation_id"], action["tool_args"]) == (
                goal,
                invocation,
                args,
            )
            approved = await approve(w, goal, result)
            assert approved.status == "committed"
        assert len(fake.drafts) == 1 and fake.sent == []


async def test_stop_cancels_gmail_reply_before_dispatch(gmail_db_url, monkeypatch):
    async with world(gmail_db_url, catalog=CATALOG) as w, gmail_domain(w, monkeypatch) as fake:
        goal = await w.goal(_grant(SEND))
        _, token = await w.invocation(goal, _grant(SEND))
        args = reply_args()
        parked = await w.broker.call(goal, token, SEND.identity, args)
        await w.owner.execute("UPDATE switchboard.goals SET status='stopped' WHERE id=$1", goal)
        refused = await _refused(
            w.broker.execute_approved(goal, parked.effect_id, parked.approval_ref, args)
        )
        assert refused.reason is Reason.GOAL and fake.sent == []
        assert (await w.action(parked.effect_id))["status"] == "cancelled"


async def test_receipts_respect_real_runtime_schema_permissions(gmail_db_url):
    async with world(gmail_db_url, catalog=CATALOG) as w:
        async with w.domain.acquire() as conn, conn.transaction():
            await conn.execute("SET LOCAL ROLE butler_messenger_rw")
            effect = uuid4()
            await conn.execute(
                "INSERT INTO gmail_owner_sends "
                "(effect_id, account, thread_id, message_id, call_digest, state) "
                "VALUES ($1, 'owner@example.test', 't1', $2, $3, 'dispatching')",
                effect,
                f"<{effect}@example.test>",
                "a" * 64,
            )
            assert (
                await conn.fetchval(
                    "SELECT state FROM gmail_owner_sends WHERE effect_id=$1", effect
                )
                == "dispatching"
            )
        async with w.domain.acquire() as conn, conn.transaction():
            await conn.execute("SET LOCAL ROLE butler_general_rw")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetchval("SELECT count(*) FROM messenger.gmail_owner_sends")


async def test_owner_to_with_nonowner_cc_parks_and_unpinned_subject_cannot_match(
    gmail_db_url, monkeypatch
):
    async with world(gmail_db_url, catalog=CATALOG) as w, gmail_domain(w, monkeypatch) as fake:
        await w.owner.execute(
            "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src, conf, "
            "observed_at, validity, content_authority, verified) VALUES "
            "($1, 'has-email', $2, 'literal', 'relationship', 1.0, now(), 'active', 'owner', true)",
            OWNER_ENTITY,
            OWNER,
        )
        from butlers.capability_broker.recipients import classify_recipients

        own = reply_args(to=[OWNER], cc=[OWNER])
        async with w.owner.acquire() as conn:
            assert await classify_recipients(conn, SEND, own) == "owner"
            assert (
                await classify_recipients(conn, SEND, {**own, "cc": ["other@example.test"]})
                == "non_owner"
            )
        args = reply_args(to=[OWNER])
        constraints = {
            key: {"type": "exact", "value": value}
            for key, value in args.items()
            if key != "subject"
        }
        await w.domain.execute(
            "INSERT INTO approval_rules (id, tool_name, arg_constraints, description) "
            "VALUES ($1, 'gmail_send_reply', $2::jsonb, 'subject unpinned')",
            uuid4(),
            constraints,
        )
        goal = await w.goal(_grant(SEND), exposed=True)
        _, token = await w.invocation(goal, _grant(SEND))
        assert (await w.broker.call(goal, token, SEND.identity, args)).status == "parked"
        assert fake.sent == []
        # Positive control: the existing exact-pin rule remains eligible when subject is pinned too.
        await w.domain.execute(
            "UPDATE approval_rules SET arg_constraints=$1::jsonb WHERE tool_name='gmail_send_reply'",
            {key: {"type": "exact", "value": value} for key, value in args.items()},
        )
        goal = await w.goal(_grant(SEND), exposed=True)
        _, token = await w.invocation(goal, _grant(SEND))
        assert (await w.broker.call(goal, token, SEND.identity, args)).status == "committed"
        assert len(fake.sent) == 1
