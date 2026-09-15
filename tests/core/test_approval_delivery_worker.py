"""Daemon lifecycle coverage for the dormant approval delivery worker."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastmcp.server.dependencies import AccessToken

from butlers.core.approval_delivery_transport import (
    ApprovalRecoveryRuntime,
    authenticated_daemon_name,
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


def test_recovery_transport_principal_requires_bound_daemon_scope() -> None:
    token = AccessToken(
        token="synthetic",
        client_id="butler:relationship",
        scopes=["approval-recovery:source"],
        claims={"actor_type": "daemon", "butler_name": "relationship"},
    )
    assert (
        authenticated_daemon_name(token, required_scope="approval-recovery:source")
        == "relationship"
    )
    assert authenticated_daemon_name(token, required_scope="approval-recovery:switchboard") is None
    mismatched = AccessToken(
        token="synthetic",
        client_id="butler:general",
        scopes=["approval-recovery:source"],
        claims={"actor_type": "daemon", "butler_name": "relationship"},
    )
    assert authenticated_daemon_name(mismatched, required_scope="approval-recovery:source") is None


@pytest.mark.asyncio
async def test_source_runtime_reuses_correlation_without_exporting_local_fence() -> None:
    captured: list[dict[str, object]] = []

    async def _dispatch(payload: dict[str, object]) -> dict[str, object]:
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
