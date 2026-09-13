"""Daemon lifecycle coverage for the dormant approval delivery worker."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

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


@pytest.mark.asyncio
async def test_lost_heartbeat_cancels_external_await_without_terminal_write() -> None:
    claim = DeliveryClaim(
        presentation_id=uuid.uuid4(),
        presentation_generation=1,
        presentation_key="approval:test:00000000-0000-0000-0000-000000000000:p:1",
        presentation_mode="single",
        claim_token=uuid.uuid4(),
        claim_fence=1,
        reconcile_only=False,
    )
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
