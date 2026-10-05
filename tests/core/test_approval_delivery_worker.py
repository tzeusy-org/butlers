"""Daemon lifecycle coverage for the dormant approval delivery worker."""

from __future__ import annotations

import asyncio
import socket
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastmcp import FastMCP

from butlers.core.approval_delivery_authority import (
    ApprovalAuthorityTopology,
    ProtectedApprovalMCP,
    SourceApprovalAdmission,
    protected_approval_principal,
)
from butlers.core.approval_delivery_transport import (
    ApprovalRecoveryRuntime,
    RecoveryAuthorityError,
)
from butlers.core.approval_delivery_worker import (
    ApprovalDeliveryWorker,
    DeliveryClaim,
    HandoffResult,
    start_approval_delivery_worker,
    stop_approval_delivery_worker,
)


class _IdleRepository:
    cancel_ineligible_presentations = AsyncMock(return_value=0)
    claim_next = AsyncMock(return_value=None)


class _Runtime:
    resolve_verified_owner_recipient = AsyncMock(return_value="owner")
    resolve_callback_secret = AsyncMock(return_value="secret")
    handoff = AsyncMock(return_value=HandoffResult("confirmed"))
    reconcile = AsyncMock(return_value=HandoffResult("ambiguous", "provider_outcome_unknown"))


def _claim() -> DeliveryClaim:
    subject_key = "approval:relationship:00000000-0000-0000-0000-000000000000"
    return DeliveryClaim(
        presentation_id=uuid.uuid4(),
        presentation_generation=1,
        presentation_key=f"{subject_key}:p:1",
        presentation_mode="single",
        subject_kind="action",
        subject_key=subject_key,
        claim_token=uuid.uuid4(),
        claim_fence=7,
        reconcile_only=False,
    )


# Spec: REQ-approval-delivery-intent-recovery-004; unsupported transport fails closed without creating a listener; registered transport proof lives in its integration harness.
@pytest.mark.asyncio
async def test_recovery_transport_requires_kernel_peer_admission(tmp_path, monkeypatch) -> None:
    assert protected_approval_principal(audience="switchboard:approval-recovery") is None
    mcp = FastMCP("kernel-peer")

    @mcp.tool()
    def peer() -> dict:
        return {
            "issuer": protected_approval_principal(audience="switchboard:approval-recovery"),
            "wrong_audience": protected_approval_principal(audience="messenger:approval-recovery"),
        }

    with monkeypatch.context() as unsupported:
        unsupported.delattr(socket, "SO_PEERCRED", raising=False)
        endpoint = ProtectedApprovalMCP(
            mcp,
            issuer="relationship",
            audience="switchboard:approval-recovery",
            socket_path=tmp_path / "approval.sock",
        )
        with pytest.raises(RecoveryAuthorityError):
            async with endpoint:
                pytest.fail("unsupported peer admission opened a listener")
        assert list(tmp_path.iterdir()) == []
    if not hasattr(socket, "SO_PEERCRED"):
        return  # The unsupported-platform rejection above is the relevant contract.
    endpoint = ProtectedApprovalMCP(
        mcp,
        issuer="relationship",
        audience="switchboard:approval-recovery",
        socket_path=tmp_path / "approval.sock",
    )
    async with endpoint:
        assert await endpoint.call("peer", {}) == {"issuer": "relationship", "wrong_audience": None}
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(RecoveryAuthorityError):
        await endpoint.call("peer", {})

    # Single-listener repetition misses the three-companion SSE drain defect.
    # This tests actual topology initialization/teardown with fresh and reused
    # registered objects; it does not attest a source row or use a fake admission.
    def registered_pair():
        switchboard = FastMCP("topology-source-peer")
        messenger = FastMCP("topology-messenger-peer")
        switchboard.tool(name="deliver")(peer)

        @messenger.tool(name="route.execute")
        def messenger_peer() -> dict:
            return {"issuer": protected_approval_principal(audience="messenger:approval-recovery")}

        return switchboard, messenger

    for reuse_registered in (False, True):
        switchboard, messenger = registered_pair()
        for cycle in range(3):
            if not reuse_registered:
                switchboard, messenger = registered_pair()
            module = SimpleNamespace()
            source = SourceApprovalAdmission(object(), owning_schema="relationship")
            topology = ApprovalAuthorityTopology(
                sources={"relationship": source},
                switchboard_registered=switchboard,
                switchboard_module=module,
                messenger_registered=messenger,
                messenger_registry_url="http://localhost:41102/mcp",
                socket_directory=tmp_path,
            )
            async with asyncio.timeout(8), topology:
                assert await topology._dispatches["relationship"]._endpoint.call("deliver", {}) == {
                    "issuer": "relationship",
                    "wrong_audience": None,
                }
                assert await topology._call("http://localhost:41102/mcp", "route.execute", {}) == {
                    "issuer": "switchboard",
                }
                assert await topology._verifiers["relationship"].endpoint.call(
                    "verify_approval_admission",
                    {"notify_request": {}},
                ) == {"allowed": False}
            assert list(tmp_path.iterdir()) == [], (reuse_registered, cycle)
            assert module._approval_recovery_call is None
            assert module._approval_recovery_verifiers == {}

    # Exercise production defaults before shortening the owned deadline. A
    # valid reply between HTTPX's implicit five seconds and our ten-second
    # deadline must succeed, rather than merely normalize an earlier refusal.
    from butlers.core import approval_delivery_authority

    silent = FastMCP("silent-approval-peer")

    @silent.tool()
    async def delayed_reply() -> dict:
        await asyncio.sleep(6)
        return {"replied": True}

    @silent.tool()
    async def no_reply() -> dict:
        await asyncio.Event().wait()
        return {}  # Unreachable unless the test's silence control is broken.

    async with ProtectedApprovalMCP(
        silent,
        issuer="relationship",
        audience="switchboard:approval-recovery",
        socket_path=tmp_path / "silent.sock",
    ) as silent_endpoint:
        async with asyncio.timeout(12):
            assert await silent_endpoint.call("delayed_reply", {}) == {"replied": True}
        # Keep a real HTTPX phase timeout content-blind too. This test-only
        # client policy forces the HTTP timeout family through the real socket;
        # it does not install a principal, fake a reply or attest a source row.
        original_client = approval_delivery_authority.httpx.AsyncClient

        class PhaseBoundedClient(original_client):
            def __init__(self, **kwargs):
                kwargs["timeout"] = 0.1
                super().__init__(**kwargs)

        with monkeypatch.context() as phase_timeout:
            phase_timeout.setattr(
                approval_delivery_authority.httpx, "AsyncClient", PhaseBoundedClient
            )
            with pytest.raises(
                RecoveryAuthorityError, match="Approval recovery authority rejected"
            ):
                async with asyncio.timeout(2):
                    await silent_endpoint.call("no_reply", {})
        # A nonreplying actual peer is bounded by the owned complete-call
        # deadline. Library lifecycle and HTTP transport remain real.
        monkeypatch.setattr(approval_delivery_authority, "_CALL_TIMEOUT_SECONDS", 0.1)
        with pytest.raises(RecoveryAuthorityError, match="Approval recovery authority rejected"):
            async with asyncio.timeout(2):
                await silent_endpoint.call("no_reply", {})
        # Also execute the deadline -> authority error -> worker uncertainty
        # boundary. This protocol-double repository proves the exception path,
        # while the owning PG transport parameter proves durable quarantine.
        claim = _claim()
        repository = SimpleNamespace(
            cancel_ineligible_presentations=AsyncMock(return_value=0),
            claim_next=AsyncMock(return_value=claim),
            load_render_subject=AsyncMock(return_value=object()),
            mark_handoff_started=AsyncMock(return_value=True),
            heartbeat=AsyncMock(return_value=True),
            complete_handoff=AsyncMock(return_value=True),
        )

        async def silent_dispatch(_claim, _payload):
            return await silent_endpoint.call("no_reply", {})

        runtime = ApprovalRecoveryRuntime(
            source_butler="relationship",
            owning_schema="relationship",
            dispatch=silent_dispatch,
            resolve_owner_recipient=AsyncMock(return_value="owner"),
            resolve_callback_secret=AsyncMock(return_value="synthetic-secret"),
        )
        worker = ApprovalDeliveryWorker(
            repository,
            SimpleNamespace(render_single=Mock(return_value={"safe": "envelope"})),
            runtime,
        )
        async with asyncio.timeout(2):
            assert await worker.process_one() is True
        repository.mark_handoff_started.assert_awaited_once_with(claim)
        repository.complete_handoff.assert_awaited_once_with(
            claim, HandoffResult("ambiguous", "provider_outcome_unknown")
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_source_runtime_reuses_correlation_without_exporting_local_fence() -> None:
    captured: list[dict[str, object]] = []

    async def _dispatch(
        local_claim: DeliveryClaim, payload: dict[str, object]
    ) -> dict[str, object]:
        assert local_claim == claim
        captured.append(payload)
        return {"handoff": {"classification": "confirmed", "provider_reference": "ref-1"}}

    runtime = ApprovalRecoveryRuntime(
        source_butler="relationship",
        owning_schema="relationship",
        dispatch=_dispatch,
        resolve_owner_recipient=AsyncMock(return_value="owner-synthetic"),
        resolve_callback_secret=AsyncMock(return_value="callback-synthetic"),
    )
    claim = _claim()
    envelope = {
        "schema_version": "notify.v1",
        "origin_butler": "relationship",
        "delivery": {
            "intent": "approval_request",
            "channel": "telegram",
            "message": "Synthetic approval.",
            "recipient": "owner-synthetic",
        },
        "actions": [{"verb": "open_dashboard", "dashboard_url": "https://dashboard.example.test"}],
    }

    assert (await runtime.handoff(claim, envelope)).classification == "confirmed"
    assert (await runtime.reconcile(claim)).classification == "confirmed"

    handoff_recovery = captured[0]["recovery"]
    reconcile_recovery = captured[1]["recovery"]
    assert handoff_recovery == {**reconcile_recovery, "operation": "handoff"}
    assert reconcile_recovery["operation"] == "reconcile"
    assert "claim_token" not in handoff_recovery and "claim_fence" not in handoff_recovery
    assert captured[1]["delivery"] == {
        "intent": "approval_request",
        "channel": "telegram",
        "message": "",
    }


@pytest.mark.asyncio
async def test_lost_heartbeat_cancels_external_await_without_terminal_write() -> None:
    claim = _claim()
    repository = SimpleNamespace(
        cancel_ineligible_presentations=AsyncMock(return_value=0),
        claim_next=AsyncMock(return_value=claim),
        load_render_subject=AsyncMock(return_value=object()),
        record_prestart_retry=AsyncMock(return_value=True),
        mark_handoff_started=AsyncMock(return_value=True),
        heartbeat=AsyncMock(return_value=False),
        complete_handoff=AsyncMock(return_value=True),
    )
    handoff_started = asyncio.Event()
    handoff_cancelled = asyncio.Event()

    async def _handoff(*_args: object) -> HandoffResult:
        handoff_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            handoff_cancelled.set()

    runtime = SimpleNamespace(
        resolve_verified_owner_recipient=AsyncMock(return_value="owner"),
        resolve_callback_secret=AsyncMock(return_value="secret"),
        handoff=_handoff,
    )
    renderer = SimpleNamespace(render_single=Mock(return_value={"safe": "envelope"}))
    worker = ApprovalDeliveryWorker(repository, renderer, runtime, heartbeat_interval_s=0.01)

    assert await worker.process_one() is True

    assert handoff_started.is_set()
    assert handoff_cancelled.is_set()
    repository.complete_handoff.assert_not_awaited()


@pytest.mark.asyncio
# Spec: REQ-approval-delivery-intent-recovery-003; active module lifecycle and server-held rollout govern worker startup.
async def test_lifecycle_starts_once_only_for_active_approvals_and_stops_cleanly() -> None:
    repository = _IdleRepository()
    module = SimpleNamespace(
        name="approvals",
        approval_delivery_worker_enabled=AsyncMock(return_value=True),
        approval_delivery_worker_components=Mock(return_value=(repository, SimpleNamespace())),
    )
    daemon = SimpleNamespace(
        config=SimpleNamespace(name="relationship"),
        _modules=[module],
        _module_runtime_states={"approvals": SimpleNamespace(health="active", enabled=True)},
        _approval_delivery_runtime=_Runtime(),
        _approval_delivery_task=None,
        _approval_delivery_stop=None,
    )

    assert await start_approval_delivery_worker(daemon) is True
    first_task = daemon._approval_delivery_task
    assert await start_approval_delivery_worker(daemon) is False
    assert daemon._approval_delivery_task is first_task
    await asyncio.sleep(0)
    await stop_approval_delivery_worker(daemon)
    assert daemon._approval_delivery_task is None
    assert daemon._approval_delivery_stop is None

    assert await start_approval_delivery_worker(daemon) is True
    assert daemon._approval_delivery_task is not first_task
    await stop_approval_delivery_worker(daemon)

    daemon._module_runtime_states["approvals"].enabled = False
    assert await start_approval_delivery_worker(daemon) is False

    daemon._module_runtime_states["approvals"].enabled = True
    module.approval_delivery_worker_enabled.return_value = False
    assert await start_approval_delivery_worker(daemon) is False

    module.approval_delivery_worker_enabled.side_effect = RuntimeError("invalid rollout config")
    assert await start_approval_delivery_worker(daemon) is False
    assert module.approval_delivery_worker_components.call_count == 2
