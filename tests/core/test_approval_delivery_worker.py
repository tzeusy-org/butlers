"""Daemon lifecycle coverage for the dormant approval delivery worker."""

from __future__ import annotations

import asyncio
import logging
import socket
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

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


def _cohost_daemons():
    """Registered peers and an idle source, without a DB or provider side effect."""
    repository = _IdleRepository()
    approvals = SimpleNamespace(
        name="approvals",
        approval_delivery_worker_enabled=AsyncMock(return_value=True),
        approval_delivery_worker_components=Mock(return_value=(repository, SimpleNamespace())),
    )
    switchboard = SimpleNamespace(name="switchboard")
    daemons = {}
    for name, modules, port in (
        ("switchboard", [switchboard], 41100),
        ("messenger", [], 41104),
        ("custom_source", [approvals], 41105),
    ):
        daemons[name] = SimpleNamespace(
            config=SimpleNamespace(name=name, db_schema=name, port=port),
            _modules=modules,
            _module_runtime_states={
                module.name: SimpleNamespace(health="active", enabled=True) for module in modules
            },
            _approval_delivery_runtime=None,
            _approval_delivery_task=None,
            _approval_delivery_stop=None,
            _credential_store=SimpleNamespace(resolve=AsyncMock(return_value="synthetic-secret")),
            _resolve_default_notify_recipient=AsyncMock(return_value="100200300"),
            db=SimpleNamespace(pool=object()),
            mcp=FastMCP(name),
            start=AsyncMock(),
            shutdown=AsyncMock(),
        )

    @daemons["switchboard"].mcp.tool()
    async def deliver(notify_request: dict, source_butler: str) -> dict:
        raise AssertionError("Idle source must never deliver")

    @daemons["messenger"].mcp.tool(name="route.execute")
    async def route_execute() -> dict:
        raise AssertionError("Idle source must never send")

    return daemons, approvals, switchboard


@pytest.mark.asyncio
async def test_start_all_owns_approval_composition_and_cancellation_cleanup() -> None:
    """Actual host startup installs recovery, then stops it before daemon teardown."""
    from butlers.cli import _start_all

    daemons, _, switchboard = _cohost_daemons()
    source = daemons["custom_source"]
    stop = asyncio.Event()
    entered = asyncio.Event()
    sockets = []

    async def wait_for_shutdown():
        runtime = source._approval_delivery_runtime
        assert isinstance(runtime, ApprovalRecoveryRuntime)
        assert source._approval_delivery_task is not None
        assert all(daemon.start.await_count == 1 for daemon in daemons.values())
        endpoint = runtime._dispatch._endpoint
        sockets.append(endpoint._socket_path)
        assert sockets[-1].exists()
        entered.set()
        await stop.wait()

    async def check_shutdown():
        assert source._approval_delivery_runtime is None
        assert source._approval_delivery_task is None
        assert all(not path.exists() for path in sockets)
        assert switchboard._approval_recovery_call is None

    source.shutdown.side_effect = check_shutdown
    real_event = asyncio.Event

    # Only the host's signal Event is substituted; worker/listener Events remain real.
    class HostEvent:
        def set(self):
            stop.set()

        async def wait(self):
            await wait_for_shutdown()

    event_calls = 0

    def event_factory():
        nonlocal event_calls
        event_calls += 1
        return HostEvent() if event_calls == 1 else real_event()

    with (
        patch("butlers.daemon.ButlerDaemon", side_effect=lambda path: daemons[path.name]),
        patch("butlers.cli._record_deployment_boot", new_callable=AsyncMock),
        patch.object(asyncio.get_running_loop(), "add_signal_handler"),
        patch("butlers.cli.asyncio.Event", side_effect=event_factory),
    ):
        task = asyncio.create_task(_start_all({name: Path(name) for name in daemons}))
        entered_wait = asyncio.create_task(entered.wait())
        try:
            done, _ = await asyncio.wait(
                [task, entered_wait],
                timeout=10,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done:
                await task  # Preserve a real assertion failure from the composition seam.
            assert entered.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            entered_wait.cancel()
            await asyncio.gather(entered_wait, return_exceptions=True)
            stop.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    for daemon in daemons.values():
        daemon.shutdown.assert_awaited_once()

    # Cancellation inside a later daemon's start cleans both its partial
    # resources and the already started peer, without ever installing authority.
    daemons, _, _ = _cohost_daemons()
    daemons["custom_source"].start.side_effect = asyncio.CancelledError
    with (
        patch("butlers.daemon.ButlerDaemon", side_effect=lambda path: daemons[path.name]),
        patch.object(asyncio.get_running_loop(), "add_signal_handler"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await _start_all({name: Path(name) for name in daemons})
    daemons["switchboard"].shutdown.assert_awaited_once()
    daemons["custom_source"].shutdown.assert_awaited_once()
    daemons["messenger"].start.assert_not_awaited()


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


# Spec: REQ-approval-delivery-intent-recovery-002, REQ-approval-delivery-intent-recovery-004; closed timeout diagnostics and unsupported peer admission; registered transport proof lives in its integration harness.
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
        # Force a typed initialization fault through the actual SDK connection
        # runner. Its RuntimeError wrapper must not export the transport error.
        from butlers.core import approval_delivery_authority

        original_client = approval_delivery_authority.httpx.AsyncClient
        initialization_fault = approval_delivery_authority.httpx.ReadTimeout("synthetic init")

        class InitializationFaultClient(original_client):
            async def send(self, *args, **kwargs):
                raise initialization_fault

        with monkeypatch.context() as initialization:
            initialization.setattr(
                approval_delivery_authority.httpx, "AsyncClient", InitializationFaultClient
            )
            with pytest.raises(RecoveryAuthorityError) as timeout:
                await endpoint.call("peer", {})
            assert str(timeout.value) == "Approval recovery authority rejected."
            assert timeout.value.__cause__ is None and timeout.value.__suppress_context__
            # A suggestive message cannot turn an unrelated SDK cause into a
            # timeout. Preserve the real wrapper and its non-timeout cause.
            initialization_fault = RuntimeError("httpx.ReadTimeout")
            with pytest.raises(RuntimeError) as unrelated:
                await endpoint.call("peer", {})
            assert unrelated.value.__cause__ is initialization_fault
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
        "actions": [
            {
                "verb": "open_dashboard",
                "reference": {"kind": "self", "route_key": "approvals_queue", "params": {}},
            }
        ],
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
async def test_lifecycle_starts_once_only_for_active_approvals_and_stops_cleanly(
    monkeypatch,
) -> None:
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

    from butlers.core import approval_delivery_authority as authority
    from butlers.core.approval_delivery_authority import approval_delivery_composition

    # Disabled/invalid/missing peers never create even a temporary socket home.
    real_directory = authority.tempfile.TemporaryDirectory
    for case in ("disabled", "invalid", "inactive", "split", "schema", "missing-tool"):
        daemons, approvals, _switchboard = _cohost_daemons()
        source = daemons["custom_source"]
        if case == "disabled":
            approvals.approval_delivery_worker_enabled.return_value = False
        elif case == "invalid":
            approvals.approval_delivery_worker_enabled.side_effect = ValueError("invalid flag")
        elif case == "inactive":
            source._module_runtime_states["approvals"].enabled = False
        elif case == "split":
            del daemons["switchboard"]
        elif case == "schema":
            source.config.db_schema = "someone_else"
        elif case == "missing-tool":
            # A failed registration closes any partially opened topology too.
            daemons["switchboard"].mcp = FastMCP("missing-deliver")
        with monkeypatch.context() as scope:
            directories = []
            directory_guard = Mock(
                side_effect=AssertionError("disabled composition opened a directory")
            )
            admission_guard = Mock(
                side_effect=AssertionError("disabled composition constructed source authority")
            )
            if case != "missing-tool":
                scope.setattr(authority, "SourceApprovalAdmission", admission_guard)
                scope.setattr(
                    "butlers.core.approval_delivery_authority.tempfile.TemporaryDirectory",
                    directory_guard,
                )
            else:

                def observed_directory(*args, directories=directories, **kwargs):
                    directory = real_directory(*args, **kwargs)
                    directories.append(Path(directory.name))
                    return directory

                scope.setattr(authority.tempfile, "TemporaryDirectory", observed_directory)
            async with approval_delivery_composition(list(daemons.values())) as topology:
                assert topology is None, case
            assert source._approval_delivery_runtime is None, case
            assert source._approval_delivery_task is None, case
            directory_guard.assert_not_called()
            admission_guard.assert_not_called()
            assert all(not directory.exists() for directory in directories)

    daemons, approvals, switchboard = _cohost_daemons()
    source = daemons["custom_source"]
    owner_lookup = AsyncMock(return_value=object())
    monkeypatch.setattr("butlers.identity.resolve_owner_channel_via_definer", owner_lookup)
    async with approval_delivery_composition(list(daemons.values())) as topology:
        assert topology is not None
        assert set(topology._sources) == {"custom_source"}
        runtime = source._approval_delivery_runtime
        assert await runtime.resolve_verified_owner_recipient() == "100200300"
        owner_lookup.assert_awaited_once_with(source.db.pool, "telegram", "100200300")
        owner_lookup.return_value = None
        assert await runtime.resolve_verified_owner_recipient() is None
        assert await runtime.resolve_callback_secret() == "synthetic-secret"
        source._credential_store.resolve.assert_awaited_once_with(
            "APPROVAL_CALLBACK_SECRET", env_fallback=False
        )
        first_task = source._approval_delivery_task
        assert await start_approval_delivery_worker(source) is False
        await stop_approval_delivery_worker(source)
        assert await start_approval_delivery_worker(source) is True
        assert source._approval_delivery_task is not first_task
        # A concurrent installation must never take custody from this owner.
        async with approval_delivery_composition(list(daemons.values())) as other:
            assert other is None
            assert source._approval_delivery_runtime is runtime
    assert source._approval_delivery_runtime is None
    assert source._approval_delivery_task is None
    assert switchboard._approval_recovery_call is None
    assert topology._sources["custom_source"]._retired
    assert not topology._socket_directory.exists()

    # Reused registrations can start again, with a fresh source epoch.
    previous_epoch = topology._sources["custom_source"].epoch
    async with approval_delivery_composition(list(daemons.values())) as restarted:
        assert restarted is not None
        assert restarted._sources["custom_source"].epoch != previous_epoch

    # Failure after one worker starts must stop it before its live topology
    # closes, and must clear the partially attached second runtime as well.
    second_daemons, _, _ = _cohost_daemons()
    second = second_daemons["custom_source"]
    second.config.name = second.config.db_schema = "another_source"
    starts = 0

    async def fail_second_start(daemon):
        nonlocal starts
        starts += 1
        if starts == 2:
            raise RuntimeError("controlled worker setup failure")
        return await start_approval_delivery_worker(daemon)

    with patch(
        "butlers.core.approval_delivery_worker.start_approval_delivery_worker", fail_second_start
    ):
        async with approval_delivery_composition([*daemons.values(), second]) as failed:
            assert failed is None
    assert starts == 2
    for item in (source, second):
        assert item._approval_delivery_runtime is None
        assert item._approval_delivery_task is None
    assert switchboard._approval_recovery_call is None


_SENTINEL = "SENTINEL-7f3c"
_OUTCOME_METRIC = "approval_delivery_worker_outcomes_total"


def _outcome_samples() -> dict[tuple[str, str, str, str], float]:
    from prometheus_client import REGISTRY

    return {
        (s.labels["schema"], s.labels["stage"], s.labels["outcome"], s.labels["reason"]): s.value
        for metric in REGISTRY.collect()
        for s in metric.samples
        if s.name == _OUTCOME_METRIC
    }


def _sentinel_claim(*, reconcile_only: bool = False) -> DeliveryClaim:
    subject_key = f"approval:relationship:{_SENTINEL}-action"
    return DeliveryClaim(
        presentation_id=f"{_SENTINEL}-presentation",
        presentation_generation=1,
        presentation_key=f"{subject_key}:p:1",
        presentation_mode="single",
        subject_kind="action",
        subject_key=subject_key,
        claim_token=f"{_SENTINEL}-token",
        claim_fence=3,
        reconcile_only=reconcile_only,
    )


def _outcome_seam(scenario: str, stop: asyncio.Event) -> tuple[Any, Any]:
    claim = _sentinel_claim(reconcile_only=scenario == "reconcile")
    repository = SimpleNamespace(
        cancel_ineligible_presentations=AsyncMock(return_value=0),
        claim_next=AsyncMock(return_value=None if scenario == "idle" else claim),
        load_render_subject=AsyncMock(
            return_value=None if scenario == "subject_unavailable" else object()
        ),
        record_prestart_retry=AsyncMock(return_value=True),
        mark_handoff_started=AsyncMock(return_value=scenario != "start_refused"),
        heartbeat=AsyncMock(return_value=True),
        complete_handoff=AsyncMock(return_value=scenario != "write_refused"),
    )
    if scenario == "transaction_exit":

        def _fail_commit(*_args: object) -> None:
            stop.set()
            raise RuntimeError(f"commit {_SENTINEL}")

        repository.complete_handoff.side_effect = _fail_commit
    if scenario == "scan_failed":

        def _fail_scan() -> None:
            stop.set()
            raise RuntimeError(f"pool {_SENTINEL}")

        repository.cancel_ineligible_presentations.side_effect = _fail_scan
    runtime = SimpleNamespace(
        resolve_verified_owner_recipient=AsyncMock(
            return_value=None if scenario == "owner_missing" else f"owner-{_SENTINEL}"
        ),
        resolve_callback_secret=AsyncMock(return_value=f"secret-{_SENTINEL}"),
        handoff=AsyncMock(return_value=HandoffResult("confirmed", None, f"ref-{_SENTINEL}")),
        reconcile=AsyncMock(side_effect=RuntimeError(f"provider {_SENTINEL}")),
    )
    if scenario == "provider_raised":
        runtime.handoff.side_effect = RuntimeError(f"provider {_SENTINEL}")
    return repository, runtime


_SEND = ("claim", "send", "none")
_OUTCOME_SCENARIOS = {
    "idle": [("scan", "idle", "none")],
    "confirmed": [_SEND, ("handoff", "confirmed", "none")],
    "owner_missing": [_SEND, ("handoff", "safe_retry", "owner_recipient_unavailable")],
    "subject_unavailable": [_SEND, ("handoff", "subject_unavailable", "none")],
    "start_refused": [_SEND, ("handoff", "start_refused", "none")],
    "write_refused": [_SEND, ("handoff", "write_refused", "none")],
    "provider_raised": [_SEND, ("handoff", "ambiguous", "provider_outcome_unknown")],
    "reconcile": [
        ("claim", "reconcile", "none"),
        ("handoff", "ambiguous", "provider_outcome_unknown"),
    ],
    "transaction_exit": [_SEND, ("scan", "failed", "none")],
    "scan_failed": [("scan", "failed", "none")],
}


# Spec: REQ-approval-delivery-intent-recovery-002, REQ-approval-delivery-intent-recovery-007; scan/claim/handoff outcomes are closed, bounded, safe, and observer failure never changes the outcome.
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", sorted(_OUTCOME_SCENARIOS))
async def test_worker_outcome_observations_are_closed_safe_and_side_effect_free(
    scenario: str, caplog: pytest.LogCaptureFixture
) -> None:
    def _repository_effects(repository: Any, runtime: Any) -> list[Any]:
        return [
            (name, [c.args for c in getattr(repository, name).await_args_list])
            for name in sorted(vars(repository))
        ] + [("handoff", runtime.handoff.await_count)]

    async def _drive(observer: Any) -> tuple[list[Any], Any]:
        stop = asyncio.Event()
        repository, runtime = _outcome_seam(scenario, stop)
        renderer = SimpleNamespace(render_single=Mock(return_value={"safe": "envelope"}))
        kwargs = {} if observer is None else {"observe": observer}
        worker = ApprovalDeliveryWorker(
            repository, renderer, runtime, schema="relationship", poll_interval_s=0.01, **kwargs
        )
        outcome: Any
        if scenario in {"transaction_exit", "scan_failed"}:
            await asyncio.wait_for(worker.run(stop), timeout=2)
            outcome = "stopped"
        else:
            outcome = await worker.process_one()
        return _repository_effects(repository, runtime), outcome

    before = _outcome_samples()
    caplog.set_level(logging.DEBUG, logger="butlers.core.approval_delivery_worker")
    effects, outcome = await _drive(None)
    after = _outcome_samples()

    expected: dict[tuple[str, str, str, str], float] = {}
    for stage, result, reason in _OUTCOME_SCENARIOS[scenario]:
        key = ("relationship", stage, result, reason)
        expected[key] = expected.get(key, 0.0) + 1.0
    delta = {k: v - before.get(k, 0.0) for k, v in after.items() if v != before.get(k, 0.0)}
    assert delta == expected
    assert all(_SENTINEL not in value for key in after for value in key)
    assert _SENTINEL not in caplog.text
    if scenario != "idle":
        assert "approval delivery" in caplog.text

    def _broken_observer(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("metrics backend down")

    assert await _drive(_broken_observer) == (effects, outcome)
