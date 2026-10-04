"""Actual registered approval-only MCP transport and owning-role proof.

No access token or trusted-source injection creates authority. Every case has
a distinct admitted positive control through kernel-authenticated Unix MCP,
the source row verifier, Switchboard registration and the Messenger ledger.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import shutil
import socket
import sys
import tempfile
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import asyncpg
import pytest
import uvicorn
from fastmcp import Client, FastMCP

from alembic import command
from butlers.config import ButlerType
from butlers.core.approval_delivery_authority import (
    ApprovalAuthorityTopology,
    ProtectedApprovalMCP,
    SourceApprovalAdmission,
    approval_companion_mcp,
)
from butlers.core.approval_delivery_transport import RecoveryAuthorityError
from butlers.core_tools._base import ToolContext
from butlers.core_tools._routing import register_routing_tools
from butlers.db import register_jsonb_codec
from butlers.mcp_wrappers import _SpanWrappingMCP, _ToolCallLoggingMCP
from butlers.migrations import _build_alembic_config
from butlers.modules.approvals.delivery_lifecycle import (
    defer_pending_action,
    transition_pending_action,
)
from butlers.modules.approvals.delivery_recovery import (
    ApprovalDeliveryRenderer,
    ApprovalDeliveryRepository,
)
from butlers.modules.approvals.models import ActionStatus
from butlers.modules.approvals.park import park_pending_action
from butlers.modules.registry import default_registry
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_RECIPIENT = "100200300"
_MESSAGE = "rendered-approval-private-sentinel"
_PROVIDER_RAW = "raw-provider-private-sentinel"
_ERROR_RAW = "raw-error-private-sentinel"
_MESSENGER_URL = "http://localhost:41102/mcp"


@pytest.fixture(scope="module")
def authority_db_url(postgres_container) -> str:
    name = migration_db_name()
    url = create_migration_db(postgres_container, name)
    for schema, chains in (
        ("relationship", ["core", "memory", "relationship", "approvals"]),
        ("switchboard", ["core", "switchboard"]),
        ("messenger", ["core", "messenger"]),
    ):
        for chain in chains:
            command.upgrade(
                _build_alembic_config(url, chains=[chain], target_schema=schema), f"{chain}@head"
            )
    # Replay the real privileged bootstrap against disposable capability
    # state. This must not accidentally widen runtime access to peer schemas.
    _bootstrap_migration_prerequisites(
        migration_bootstrap_db_url(postgres_container, name), urlparse(url).username
    )
    return url


@pytest.fixture
async def authority_pools(authority_db_url):
    pools = {}
    # Destructive disposable-fixture cleanup belongs to the migration owner.
    # Bootstrap intentionally grants runtime DML, never TRUNCATE. All source
    # admission, routing and ledger operations below retain owning SET ROLE.
    cleanup = await asyncpg.connect(authority_db_url)
    try:
        await cleanup.execute(
            "TRUNCATE relationship.approval_delivery_attempts, "
            "relationship.approval_delivery_cohort_members, "
            "relationship.approval_delivery_presentations, "
            "relationship.approval_delivery_cohorts, "
            "relationship.approval_delivery_intents, relationship.approval_events, "
            "relationship.pending_actions, switchboard.notifications, "
            "switchboard.message_inbox, messenger.approval_delivery_handoffs CASCADE"
        )
    finally:
        await cleanup.close()
    async with AsyncExitStack() as stack:
        for schema in ("relationship", "switchboard", "messenger"):

            async def setup(connection, *, owning_schema=schema):
                await connection.execute(f'SET ROLE "butler_{owning_schema}_rw"')

            pool = await asyncpg.create_pool(
                authority_db_url,
                min_size=1,
                max_size=4,
                init=register_jsonb_codec,
                setup=setup,
                server_settings={"search_path": f"{schema},public"},
            )
            stack.push_async_callback(pool.close)
            pools[schema] = pool
            assert await pool.fetchval("SELECT current_user") == f"butler_{schema}_rw"
            for peer in ("relationship", "switchboard", "messenger"):
                if peer != schema:
                    legacy_relationship_reader = schema == "relationship" and peer == "switchboard"
                    assert (
                        await pool.fetchval("SELECT has_schema_privilege($1, 'USAGE')", peer)
                        is legacy_relationship_reader
                    )
                    peer_table = {
                        "relationship": "pending_actions",
                        "switchboard": "notifications",
                        "messenger": "approval_delivery_handoffs",
                    }[peer]
                    if legacy_relationship_reader:
                        # Existing bootstrap deliberately grants Relationship
                        # generic Switchboard reads for follow-up jobs. It is
                        # not approval-source authority or a new grant here.
                        assert await pool.fetchval(f"SELECT count(*) FROM {peer}.{peer_table}") >= 0
                        with pytest.raises(asyncpg.InsufficientPrivilegeError):
                            await pool.execute(
                                "UPDATE switchboard.notifications SET message=message WHERE false"
                            )
                    else:
                        with pytest.raises(asyncpg.InsufficientPrivilegeError):
                            await pool.fetchval(f"SELECT count(*) FROM {peer}.{peer_table}")
        source = pools["relationship"]
        await source.execute(
            "UPDATE approval_delivery_rollout SET admission_enabled=true, worker_enabled=false"
        )
        await source.execute(
            "UPDATE public.approvals_policy SET quiet_start_hour=NULL, quiet_end_hour=NULL, "
            "timezone='UTC' WHERE id=1"
        )
        owner = await source.fetchval(
            "SELECT id FROM public.entities WHERE 'owner'=ANY(roles) LIMIT 1"
        )
        if owner is None:
            owner = await source.fetchval(
                "INSERT INTO public.entities(canonical_name,entity_type,roles) "
                "VALUES ('Synthetic authority owner','person',ARRAY['owner']) RETURNING id"
            )
        await source.execute(
            "INSERT INTO entity_facts(subject,predicate,object,object_kind,src) "
            "VALUES ($1,'has-handle',$2,'literal','synthetic-approval-authority') "
            "ON CONFLICT DO NOTHING",
            owner,
            f"telegram:{_RECIPIENT}",
        )
        await source.execute(
            "INSERT INTO entity_facts(subject,predicate,object,object_kind,src) "
            "VALUES ($1,'has-email','synthetic@example.test','literal',"
            "'synthetic-approval-authority') ON CONFLICT DO NOTHING",
            owner,
        )
        switchboard = pools["switchboard"]
        await switchboard.execute(
            "INSERT INTO butler_registry(name,endpoint_url,modules,last_seen_at,eligibility_state) "
            "VALUES ('relationship','http://localhost:41101/mcp','[\"approvals\"]'::jsonb,now(),'active'),"
            "('messenger',$1,'[\"telegram\",\"email\",\"whatsapp\"]'::jsonb,now(),'active') "
            "ON CONFLICT(name) DO UPDATE SET last_seen_at=now(),eligibility_state='active'",
            _MESSENGER_URL,
        )
        yield pools


class _SyntheticProvider:
    def __init__(self, channel):
        self.name = channel
        self.calls = []
        self.uncertain = False

    async def _send_message(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.uncertain:
            raise RuntimeError(_ERROR_RAW)
        return {"message_id": "safe-provider-ref", "raw": _PROVIDER_RAW}

    async def _send_email(self, *args, **kwargs):
        return await self._send_message(*args, **kwargs)


async def _registered_servers(pools):
    switchboard = default_registry().load_from_config({"switchboard": {"groups": ["routing"]}})[0]
    switchboard_mcp = FastMCP("actual-switchboard-registration")
    await switchboard.register_tools(
        _SpanWrappingMCP(switchboard_mcp, "switchboard", module_name="switchboard"),
        SimpleNamespace(groups=["routing"]),
        SimpleNamespace(pool=pools["switchboard"]),
        "switchboard",
    )
    providers = {name: _SyntheticProvider(name) for name in ("telegram", "email", "whatsapp")}
    daemon = SimpleNamespace(
        config=SimpleNamespace(name="messenger", trusted_route_callers=("switchboard",)),
        db=SimpleNamespace(pool=pools["messenger"]),
        _modules=list(providers.values()),
    )
    messenger_mcp = FastMCP("actual-messenger-registration")
    messenger_registration = _ToolCallLoggingMCP(messenger_mcp, "messenger", module_name="core")
    register_routing_tools(
        ToolContext(
            daemon, pools["messenger"], None, "messenger", ButlerType.STAFFER, False, True, None
        ),
        messenger_registration,
        lambda *_args, **_kwargs: messenger_registration.tool(),
    )
    return switchboard, switchboard_mcp, messenger_mcp, providers


async def _claimed_presentation(pool, *, ordinal, cohort=False):
    count = 4 if cohort else 1
    for index in range(count):
        now = datetime.now(UTC)
        await park_pending_action(
            pool,
            action_id=uuid.uuid4(),
            tool_name="relationship_assert_fact",
            tool_args={"ordinal": ordinal, "index": index},
            agent_summary="Synthetic admitted source",
            requested_at=now,
            expires_at=now + timedelta(hours=2),
            why=_MESSAGE,
            evidence=[],
            blast_radius="contact",
            reversibility="compensable",
            origin_butler="relationship",
        )
    repo = ApprovalDeliveryRepository(pool)
    if cohort:
        # Schedule the unrelated direct subjects later; do not manufacture a
        # provider outcome to make the digest become the next eligible claim.
        await pool.execute(
            "UPDATE approval_delivery_presentations SET next_attempt_at=now()+interval '1 hour' "
            "WHERE state='ready' AND subject_kind='action'"
        )
    claim = await repo.claim_next()
    assert claim is not None
    assert (claim.subject_kind == "cohort") is cohort
    subject = await repo.load_render_subject(claim)
    assert subject is not None
    renderer = ApprovalDeliveryRenderer(dashboard_base_url="https://dashboard.example.test")
    envelope = (
        renderer.render_digest(subject, owner_recipient=_RECIPIENT)
        if cohort
        else renderer.render_single(
            subject, owner_recipient=_RECIPIENT, callback_secret="synthetic-callback-secret"
        )
    )
    envelope["recovery"] = {
        "operation": "handoff",
        "subject_kind": claim.subject_kind,
        "subject_key": claim.subject_key,
        "presentation_key": claim.presentation_key,
        "presentation_generation": claim.presentation_generation,
        "presentation_mode": claim.presentation_mode,
    }
    assert await repo.mark_handoff_started(claim)
    return repo, claim, envelope


@asynccontextmanager
async def _ordinary_tcp_mcp(registered):
    """Actual public HTTP listener with no approval peer principal installed."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    server = uvicorn.Server(
        uvicorn.Config(
            registered.http_app(path="/mcp", stateless_http=True),
            access_log=False,
            log_level="warning",
            timeout_graceful_shutdown=2,
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    pytest.fail("public synthetic MCP did not start")
                await asyncio.sleep(0.01)
        async with Client(f"http://127.0.0.1:{listener.getsockname()[1]}/mcp") as client:
            yield client
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 3)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        listener.close()


_CASES = [
    "accepted",
    "cohort",
    "missing",
    "forged",
    "expired",
    "revoked",
    "revoked-during-verify",
    "retired-during-mint",
    "restart",
    "retired",
    "wrong-audience",
    "wrong-source",
    "wrong-schema",
    "wrong-subject",
    "wrong-generation",
    "wrong-mode",
    "wrong-operation",
    "wrong-digest",
    "caller-dto",
    "caller-endpoint",
    "fabricated-claim",
    "terminal-race",
    "defer-race",
    "lease-succession",
    "forged-handoff-after-succession",
    "foreign-pid",
    "unavailable-verifier",
    "reconcile",
    "telegram-uncertain",
    "email-uncertain",
    "whatsapp-uncertain",
    "ordinary-absent",
    "ordinary-null",
    "public-malformed",
]


# Spec: REQ-approval-delivery-intent-recovery-002, REQ-approval-delivery-intent-recovery-004,
# REQ-approval-delivery-intent-recovery-009, REQ-approval-delivery-intent-recovery-011,
# REQ-core-notify-028, REQ-core-notify-029, REQ-core-notify-031,
# REQ-butler-messenger-001, REQ-butler-messenger-002.
@pytest.mark.parametrize("case", _CASES)
async def test_registered_source_authority_transport(authority_pools, case, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="butlers.mcp_wrappers")
    from butlers import mcp_wrappers

    captures, spans = [], []
    actual_capture, actual_span = mcp_wrappers.capture_tool_call, mcp_wrappers.tool_span

    def observed_capture(**kwargs):
        captures.append(copy.deepcopy(kwargs))
        return actual_capture(**kwargs)

    def observed_span(*args, **kwargs):
        spans.append((args, kwargs))
        return actual_span(*args, **kwargs)

    # Observe the actual generic proxy without fabricating a session, token,
    # admission result or tool-capture persistence context.
    monkeypatch.setattr(mcp_wrappers, "capture_tool_call", observed_capture)
    monkeypatch.setattr(mcp_wrappers, "tool_span", observed_span)
    pools = authority_pools
    source = SourceApprovalAdmission(
        ApprovalDeliveryRepository(pools["relationship"]), owning_schema="relationship"
    )
    minted_proofs = []
    actual_mint = source.mint

    async def observed_mint(*args, **kwargs):
        proof = await actual_mint(*args, **kwargs)
        minted_proofs.append(proof)
        return proof

    monkeypatch.setattr(source, "mint", observed_mint)
    module, switchboard_mcp, messenger_mcp, providers = await _registered_servers(pools)
    with tempfile.TemporaryDirectory(prefix="approval-", dir=Path.home()) as folder:
        sockets = Path(folder)
        topology = ApprovalAuthorityTopology(
            sources={"relationship": source},
            switchboard_registered=switchboard_mcp,
            switchboard_module=module,
            messenger_registered=messenger_mcp,
            messenger_registry_url=_MESSENGER_URL,
            socket_directory=sockets,
        )
        async with topology:
            runtime = topology.runtime(
                "relationship",
                resolve_owner_recipient=lambda: asyncio.sleep(0, result=_RECIPIENT),
                resolve_callback_secret=lambda: asyncio.sleep(0, result="synthetic-secret"),
            )
            # A distinct valid presentation defeats always-refuse/verifier-
            # unavailable vacuous greens. Negative tuples have no ledger yet.
            repo, control, envelope = await _claimed_presentation(pools["relationship"], ordinal=0)
            assert (await runtime.handoff(control, envelope)).classification == "confirmed"
            assert (await runtime.handoff(control, envelope)).classification == "confirmed"
            assert len(providers["telegram"].calls) == 1
            from butlers.core.approval_delivery_worker import HandoffResult

            assert await repo.complete_handoff(control, HandoffResult("confirmed"))
            repo, claim, payload = await _claimed_presentation(
                pools["relationship"], ordinal=1, cohort=case == "cohort"
            )
            endpoint = topology._dispatches["relationship"]._endpoint
            proof = await source.mint(claim, payload)
            forbidden = [_MESSAGE, _RECIPIENT, _PROVIDER_RAW, _ERROR_RAW, proof]
            forbidden += [
                item["callback_token"] for item in payload["actions"] if "callback_token" in item
            ]
            admitted_log_count = sum(
                record.name == "butlers.mcp_wrappers" for record in caplog.records
            )
            assert admitted_log_count >= 4
            assert len(captures) >= 4 and len(spans) >= 2
            admitted_capture_count, admitted_span_count = len(captures), len(spans)
            changed = copy.deepcopy(payload)
            if case in {"ordinary-absent", "ordinary-null", "public-malformed"}:
                if case == "ordinary-absent":
                    changed.pop("recovery")
                elif case == "ordinary-null":
                    changed["recovery"] = None
                else:
                    changed["recovery"] = {"private": _MESSAGE}
                route_args = {
                    "schema_version": "route.v1",
                    "request_context": {
                        "request_id": str(uuid.uuid4()),
                        "received_at": datetime.now(UTC).isoformat(),
                        "source_channel": "mcp",
                        "source_endpoint_identity": "switchboard",
                        "source_sender_identity": "relationship",
                    },
                    "input": {"prompt": "Deliver.", "context": {"notify_request": changed}},
                }
                async with _ordinary_tcp_mcp(messenger_mcp) as public:
                    result = (await public.call_tool("route.execute", route_args)).data
                if case == "public-malformed":
                    assert result["error"]["message"] == "Approval recovery authority rejected."
                    assert len(providers["telegram"].calls) == 1
                    assert (
                        sum(record.name == "butlers.mcp_wrappers" for record in caplog.records)
                        == admitted_log_count
                    )
                else:
                    assert result["status"] == "ok"
                    assert len(providers["telegram"].calls) == 2
                    assert len(captures) > admitted_capture_count
                    assert (
                        sum(record.name == "butlers.mcp_wrappers" for record in caplog.records)
                        > admitted_log_count
                    )
                assert (
                    await pools["messenger"].fetchval(
                        "SELECT count(*) FROM approval_delivery_handoffs"
                    )
                    == 1
                )
                # Continue through the shared rejection/privacy assertions on
                # a forged proof, independently of the ordinary TCP exercise.
                proof = "forged-proof"
            expect_handoff = case in {"accepted", "cohort", "reconcile"} or case.endswith(
                "uncertain"
            )
            if case == "missing":
                proof = None
            elif case == "forged":
                proof = "forged-proof"
            elif case == "expired":
                await asyncio.sleep(10.05)
            elif case == "revoked":
                source.revoke(proof)
            elif case in {"revoked-during-verify", "retired-during-mint"}:
                checked = asyncio.Event()
                release = asyncio.Event()
                actual_authorize = source._repository.authorize_transport

                async def paused_authorize(*args, **kwargs):
                    remaining = await actual_authorize(*args, **kwargs)
                    checked.set()
                    await release.wait()
                    return remaining

                # This wraps the real owning-role SQL; it does not fabricate
                # a row, principal, proof or verifier response.
                monkeypatch.setattr(source._repository, "authorize_transport", paused_authorize)
                if case == "revoked-during-verify":
                    pending = asyncio.create_task(
                        endpoint.call(
                            "deliver",
                            {"source_butler": "relationship", "notify_request": payload},
                            proof=proof,
                        )
                    )
                else:
                    pending = asyncio.create_task(source.mint(claim, payload))
                try:
                    await asyncio.wait_for(checked.wait(), 10)
                    if case == "revoked-during-verify":
                        source.revoke(proof)
                    else:
                        source.retire()
                    release.set()
                    if case == "revoked-during-verify":
                        assert await pending == {
                            "status": "failed",
                            "error": "Approval recovery authority rejected.",
                            "retryable": False,
                        }
                    else:
                        with pytest.raises(RecoveryAuthorityError):
                            await pending
                finally:
                    release.set()
                    if not pending.done():
                        pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
            elif case in {"restart", "retired"}:
                source.retire()
                if case == "restart":
                    replacement = SourceApprovalAdmission(repo, owning_schema="relationship")
                    assert await replacement.verify(payload, proof) == {"allowed": False}
            elif case == "wrong-source":
                changed["origin_butler"] = "health"
            elif case in {"wrong-schema", "wrong-subject"}:
                prefix = "approval:health:" if case == "wrong-schema" else "approval:relationship:"
                changed["recovery"]["subject_key"] = prefix + str(uuid.uuid4())
            elif case == "wrong-generation":
                changed["recovery"]["presentation_generation"] += 1
            elif case == "wrong-mode":
                changed["recovery"]["presentation_mode"] = "burst_digest"
            elif case == "wrong-operation":
                changed["recovery"]["operation"] = "reconcile"
            elif case == "wrong-digest":
                changed["delivery"]["message"] = "forged-body"
            elif case == "caller-dto":
                changed["_trusted_approval_recovery"] = {
                    "issuer": "relationship",
                    "owning_schema": "relationship",
                    **payload["recovery"],
                }
            elif case == "caller-endpoint":
                changed["source_verifier_url"] = "http://caller-selected.example.test/mcp"
            elif case == "fabricated-claim":
                with pytest.raises(RecoveryAuthorityError):
                    await source.mint(replace(claim, presentation_id=uuid.uuid4()), payload)
                proof = "fabricated-proof"
            elif case in {"terminal-race", "defer-race"}:
                action_id = await pools["relationship"].fetchval(
                    "SELECT action_id FROM approval_delivery_intents WHERE action_key=$1",
                    claim.subject_key,
                )
                if case == "terminal-race":
                    await transition_pending_action(
                        pools["relationship"],
                        action_id=action_id,
                        target_status=ActionStatus.REJECTED,
                        decided_by="synthetic-authenticated-dashboard",
                        event_actor="synthetic-authenticated-dashboard",
                        event_reason="Synthetic terminal race",
                    )
                else:
                    await defer_pending_action(
                        pools["relationship"],
                        action_id=action_id,
                        hours=1,
                        actor="synthetic-authenticated-dashboard",
                    )
            elif case in {"lease-succession", "forged-handoff-after-succession"}:
                await pools["relationship"].execute(
                    "UPDATE approval_delivery_presentations SET claim_expires_at=now()-interval '1 second' WHERE id=$1",
                    claim.presentation_id,
                )
                successor = await repo.claim_next()
                assert successor is not None and successor.claim_fence > claim.claim_fence
                assert successor.reconcile_only
                if case == "forged-handoff-after-succession":
                    with pytest.raises(RecoveryAuthorityError):
                        await source.mint(replace(successor, reconcile_only=False), payload)
            elif case == "unavailable-verifier":
                await topology._verifiers["relationship"].endpoint.close()
            elif case == "foreign-pid":
                # A same-UID child knows the socket path but the kernel gives it
                # a different PID. Even initialize must receive no HTTP reply.
                script = """import asyncio,sys,httpx
async def main():
    async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=sys.argv[1])) as c:
        try:
            await c.post("http://approval.local/mcp",json={"jsonrpc":"2.0","id":1,"method":"initialize","params":{}})
        except httpx.TransportError:
            return
        raise SystemExit(1)
asyncio.run(main())
"""
                child = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    script,
                    str(endpoint._socket_path),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                assert await asyncio.wait_for(child.wait(), 10) == 0
                proof = None
            elif case == "reconcile":
                providers["telegram"].uncertain = True
                original = await endpoint.call(
                    "deliver",
                    {"source_butler": "relationship", "notify_request": payload},
                    proof=proof,
                )
                assert original["handoff"]["classification"] == "ambiguous"
                original_claim = claim
                await pools["relationship"].execute(
                    "UPDATE approval_delivery_presentations SET claim_expires_at=now()-interval '1 second' WHERE id=$1",
                    original_claim.presentation_id,
                )
                claim = await repo.claim_next()
                assert claim is not None and claim.reconcile_only
                assert claim.presentation_key == original_claim.presentation_key
                assert claim.claim_fence > original_claim.claim_fence
                changed["recovery"]["operation"] = "reconcile"
                changed["delivery"] = {
                    "intent": "approval_request",
                    "channel": "telegram",
                    "message": "",
                }
                changed.pop("actions", None)
                changed.pop("decision_dossier", None)
                proof = await source.mint(claim, changed)
            elif case.endswith("uncertain"):
                channel = case.split("-", 1)[0]
                providers[channel].uncertain = True
                changed["delivery"]["channel"] = channel
                if channel == "email":
                    changed["delivery"]["recipient"] = "synthetic@example.test"
                elif channel == "whatsapp":
                    changed["delivery"]["recipient"] = f"{_RECIPIENT}@s.whatsapp.net"
                    owner = await pools["relationship"].fetchval(
                        "SELECT id FROM public.entities WHERE 'owner'=ANY(roles) LIMIT 1"
                    )
                    await pools["relationship"].execute(
                        "INSERT INTO entity_facts(subject,predicate,object,object_kind,src) VALUES ($1,'has-phone',$2,'literal','synthetic-approval-authority') ON CONFLICT DO NOTHING",
                        owner,
                        f"+{_RECIPIENT}",
                    )
                proof = await source.mint(claim, changed)
            arguments = {"source_butler": changed["origin_butler"], "notify_request": changed}
            if case == "wrong-audience":
                async with ProtectedApprovalMCP(
                    await approval_companion_mcp(switchboard_mcp, "deliver"),
                    issuer="relationship",
                    audience="wrong-audience",
                    socket_path=sockets / "wrong.sock",
                ) as wrong:
                    result = await wrong.call("deliver", arguments, proof=proof)
            else:
                result = await endpoint.call("deliver", arguments, proof=proof)
            if expect_handoff:
                expected = "confirmed" if case in {"accepted", "cohort"} else "ambiguous"
                assert result["handoff"]["classification"] == expected
                assert (await endpoint.call("deliver", arguments, proof=proof))["handoff"][
                    "classification"
                ] == expected
                expected_count = 2
                assert sum(len(provider.calls) for provider in providers.values()) == expected_count
            else:
                assert result == {
                    "status": "failed",
                    "error": "Approval recovery authority rejected.",
                    "retryable": False,
                }
                ordinary_count = 2 if case in {"ordinary-absent", "ordinary-null"} else 1
                assert sum(len(provider.calls) for provider in providers.values()) == ordinary_count
                if ordinary_count == 1:
                    assert (
                        sum(record.name == "butlers.mcp_wrappers" for record in caplog.records)
                        == admitted_log_count
                    )
                    assert len(captures) == admitted_capture_count
                    assert len(spans) == admitted_span_count
                assert (
                    await pools["messenger"].fetchval(
                        "SELECT count(*) FROM approval_delivery_handoffs"
                    )
                    == 1
                )
            assert await pools["switchboard"].fetchval("SELECT count(*) FROM notifications") == 0
            assert await pools["switchboard"].fetchval("SELECT count(*) FROM message_inbox") == 0
            persisted = await pools["messenger"].fetchval(
                "SELECT string_agg(t::text,' ') FROM approval_delivery_handoffs t"
            )
            for table in (
                "approval_delivery_intents",
                "approval_delivery_cohorts",
                "approval_delivery_cohort_members",
                "approval_delivery_presentations",
                "approval_delivery_attempts",
                "approval_events",
            ):
                source_evidence = await pools["relationship"].fetchval(
                    f"SELECT string_agg(t::text,' ') FROM {table} t"
                )
                persisted += source_evidence or ""
            for sentinel in forbidden + minted_proofs:
                assert sentinel not in persisted
                assert sentinel not in caplog.text
                if case not in {"ordinary-absent", "ordinary-null"}:
                    assert sentinel not in repr(captures)
            assert (
                await pools["relationship"].fetchval(
                    "SELECT worker_enabled FROM approval_delivery_rollout"
                )
                is False
            )
        assert list(sockets.iterdir()) == []
        assert module._approval_recovery_call is None and not module._approval_recovery_verifiers
        assert await source.verify(payload, proof) == {"allowed": False}
