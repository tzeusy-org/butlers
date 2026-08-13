"""Tests for route.execute accept-then-process async dispatch (butlers-963.6).

Verifies:
1. Accept phase: route.execute returns {"status": "accepted"} quickly without awaiting trigger
2. Persist + background dispatch: route_inbox_insert called; trigger() called in background
3. Failure/success recording: errors stored in route_inbox
4. Crash recovery: _recover_route_inbox called on startup (not for staffer)
5. Complexity plumbing: complexity forwarded to spawner.trigger()
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.dashboard_turns import DashboardTurnResult
from butlers.core.model_routing import Complexity, coerce_complexity_tier
from butlers.daemon import ButlerDaemon
from butlers.tools.switchboard.routing.contracts import parse_route_envelope

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _toml_value(v: Any) -> str:
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, list):
        items = ", ".join(f'"{i}"' if isinstance(i, str) else str(i) for i in v)
        return f"[{items}]"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _make_butler_toml(
    tmp_path: Path,
    *,
    butler_name: str = "health",
    port: int = 9200,
    butler_type: str | None = None,
    modules: dict[str, dict] | None = None,
) -> Path:
    modules = modules or {}
    toml_lines = [
        "[butler]",
        f'name = "{butler_name}"',
        f"port = {port}",
        'description = "A test butler"',
    ]
    if butler_type is not None:
        toml_lines.append(f'type = "{butler_type}"')
    toml_lines += [
        "",
        "[butler.db]",
        'name = "butlers"',
        f'schema = "{butler_name}"',
        "",
        "[[butler.schedule]]",
        'name = "daily-check"',
        'cron = "0 9 * * *"',
        'prompt = "Do the daily check"',
    ]
    for mod_name, mod_cfg in modules.items():
        toml_lines.append(f"\n[modules.{mod_name}]")
        for k, v in mod_cfg.items():
            toml_lines.append(f"{k} = {_toml_value(v)}")
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


def _patch_infra(butler_name: str = "health"):
    mock_pool = AsyncMock()
    mock_pool.fetchval.return_value = None
    mock_db = MagicMock()
    mock_db.provision = AsyncMock()
    mock_db.connect = AsyncMock(return_value=mock_pool)
    mock_db.close = AsyncMock()
    mock_db.pool = mock_pool
    mock_db.user = "postgres"
    mock_db.password = "postgres"
    mock_db.host = "localhost"
    mock_db.port = 5432
    mock_db.db_name = "butlers"

    mock_spawner = MagicMock()
    mock_spawner.stop_accepting = MagicMock()
    mock_spawner.drain = AsyncMock()

    mock_adapter = MagicMock()
    mock_adapter.binary_name = "claude"
    mock_adapter_cls = MagicMock(return_value=mock_adapter)

    return {
        "db_from_env": patch("butlers.lifecycle.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.lifecycle.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.lifecycle.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.lifecycle.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "init_telemetry": patch("butlers.lifecycle.init_telemetry"),
        "sync_schedules": patch("butlers.lifecycle.sync_schedules", new_callable=AsyncMock),
        "get_adapter": patch("butlers.lifecycle.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "mock_db": mock_db,
        "mock_pool": mock_pool,
        "mock_spawner": mock_spawner,
    }


def _configure_owned_processing_lease(patches: dict[str, Any]) -> None:
    """Make post-start route work own its mocked processing lease."""
    lease_conn = AsyncMock()
    lease_conn.fetchval.return_value = uuid.uuid4()
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=lease_conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=False)
    patches["mock_pool"].acquire = MagicMock(return_value=acquire_cm)


async def _start_daemon_with_route_execute(butler_dir: Path, patches: dict):
    """Boot a daemon and capture the route.execute handler function."""
    route_execute_fn = None
    mock_mcp = MagicMock()

    def tool_decorator(*_decorator_args, **decorator_kwargs):
        declared_name = decorator_kwargs.get("name")

        def decorator(fn):
            nonlocal route_execute_fn
            resolved_name = declared_name or fn.__name__
            if resolved_name == "route.execute":
                route_execute_fn = fn
            return fn

        return decorator

    mock_mcp.tool = tool_decorator

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
        patches["init_telemetry"],
        patches["sync_schedules"],
        patch("butlers.lifecycle.FastMCP", return_value=mock_mcp),
        patch("butlers.lifecycle.Spawner", return_value=patches["mock_spawner"]),
        patches["get_adapter"],
        patches["shutil_which"],
        patches["start_mcp_server"],
        patches["connect_switchboard"],
        patches["recover_route_inbox"],
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()

    _configure_owned_processing_lease(patches)
    return daemon, route_execute_fn


def _route_request_context(
    *,
    source_endpoint_identity: str = "switchboard",
    source_sender_identity: str = "health",
    source_channel: str = "telegram_bot",
) -> dict[str, Any]:
    return {
        "request_id": "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
        "received_at": "2026-02-18T10:00:00Z",
        "source_channel": source_channel,
        "source_endpoint_identity": source_endpoint_identity,
        "source_sender_identity": source_sender_identity,
    }


def _make_trigger_mock():
    trigger_mock = AsyncMock()
    trigger_result = MagicMock()
    trigger_result.session_id = uuid.uuid4()
    trigger_result.success = True
    trigger_result.error = None
    trigger_mock.return_value = trigger_result
    return trigger_mock


def _dashboard_turn_result(
    outcome: str,
    *,
    message_id: uuid.UUID,
    request_id: uuid.UUID,
    inbox_id: uuid.UUID | None = None,
) -> DashboardTurnResult:
    return DashboardTurnResult(
        outcome=outcome,
        message_id=message_id,
        conversation_id=uuid.uuid4(),
        request_id=request_id,
        target_butler="health",
        target_kind="route",
        route_inbox_id=inbox_id,
        cancel_requested_at=None,
        cancel_confirmed_at=None,
        terminal_state=None,
        terminal_at=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_accept_phase_and_background_dispatch(tmp_path: Path) -> None:
    """Returns accepted in <100ms; route_inbox_insert called before return; trigger called in background with correct params."""
    patches = _patch_infra("health")
    butler_dir = _make_butler_toml(tmp_path, butler_name="health")
    daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
    assert route_execute_fn is not None

    # Accept phase: fast return with accepted status
    inserted_id = uuid.uuid4()
    mock_insert = AsyncMock(return_value=inserted_id)
    with patch("butlers.core_tools._routing.route_inbox_insert", mock_insert):
        t0 = time.monotonic()
        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
        )
        elapsed_ms = (time.monotonic() - t0) * 1000

    assert result["status"] == "accepted"
    assert result["schema_version"] == "route_response.v1"
    assert result["inbox_id"] == str(inserted_id)
    assert elapsed_ms < 100, f"Accept phase took {elapsed_ms:.0f}ms, expected <100ms"

    # route_inbox_insert called with full envelope
    mock_insert.assert_awaited_once()
    envelope = mock_insert.call_args.kwargs["route_envelope"]
    assert envelope["schema_version"] == "route.v1"
    assert envelope["input"]["prompt"] == "Run health check."

    # Background trigger called with trigger_source=route and request_id
    trigger_mock = _make_trigger_mock()
    daemon.spawner.trigger = trigger_mock
    with (
        patch(
            "butlers.core_tools._routing.route_inbox_insert",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_claim_processing",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core.route_inbox.route_inbox_renew_processing_claim",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_mark_processed",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
        )
        await asyncio.sleep(0.05)

    trigger_mock.assert_awaited()
    call_kwargs = trigger_mock.call_args.kwargs
    assert call_kwargs["trigger_source"] == "route"
    assert call_kwargs["request_id"] == "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b"


async def test_dashboard_route_acceptance_atomically_claims_and_enqueues_before_spawning(
    tmp_path: Path,
) -> None:
    """A target cannot schedule a dashboard runtime outside the Stop transaction."""
    patches = _patch_infra("health")
    butler_dir = _make_butler_toml(tmp_path, butler_name="health")
    daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
    assert route_execute_fn is not None

    message_id = uuid.uuid4()
    request_id = uuid.UUID("018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b")
    inbox_id = uuid.uuid4()
    conn = AsyncMock()
    transaction_cm = MagicMock()
    transaction_cm.__aenter__ = AsyncMock(return_value=conn)
    transaction_cm.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=transaction_cm)
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=False)
    patches["mock_pool"].acquire = MagicMock(return_value=acquire_cm)
    conn.fetchval = AsyncMock(return_value=None)

    claim = AsyncMock(
        return_value=_dashboard_turn_result("active", message_id=message_id, request_id=request_id)
    )
    insert = AsyncMock(return_value=inbox_id)
    mark = AsyncMock(
        return_value=_dashboard_turn_result(
            "active",
            message_id=message_id,
            request_id=request_id,
            inbox_id=inbox_id,
        )
    )
    trigger = _make_trigger_mock()
    daemon.spawner.trigger = trigger

    with (
        patch("butlers.core_tools._routing.claim_target", claim),
        patch("butlers.core_tools._routing.route_inbox_insert_on_connection", insert),
        patch("butlers.core_tools._routing.mark_route_enqueued", mark),
        patch(
            "butlers.core_tools._routing.route_inbox_claim_processing",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core.route_inbox.route_inbox_renew_processing_claim",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_mark_processed",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_insert", new_callable=AsyncMock
        ) as legacy_insert,
    ):
        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(source_channel="dashboard"),
            input={"prompt": "Record this fact."},
            source_metadata={
                "channel": "dashboard",
                "identity": "dashboard:owner",
                "tool_name": "ingest",
                "dashboard_message_id": str(message_id),
            },
        )
        await asyncio.sleep(0.05)

    assert result["status"] == "accepted"
    assert result["inbox_id"] == str(inbox_id)
    claim.assert_awaited_once_with(
        conn,
        message_id=message_id,
        request_id=request_id,
        target_butler="health",
    )
    insert.assert_awaited_once()
    assert insert.await_args.args == (conn,)
    assert insert.await_args.kwargs["route_envelope"]["source_metadata"][
        "dashboard_message_id"
    ] == str(message_id)
    mark.assert_awaited_once_with(conn, message_id=message_id, route_inbox_id=inbox_id)
    legacy_insert.assert_not_awaited()
    assert trigger.call_args.kwargs["dashboard_turn_id"] == message_id


@pytest.mark.parametrize("outcome", ["ambiguous", "active", "cancelling"])
async def test_dashboard_processing_recovery_never_replays_a_predecessor(
    outcome: str,
) -> None:
    """Every non-terminal recovery result fails closed rather than replaying."""
    from types import SimpleNamespace

    from butlers.switchboard_wiring import recover_route_inbox

    pool = AsyncMock()
    message_id = uuid.uuid4()
    row_id = uuid.uuid4()
    trigger = _make_trigger_mock()
    daemon = SimpleNamespace(
        config=SimpleNamespace(name="health"),
        spawner=SimpleNamespace(trigger=trigger),
    )
    envelope = {
        "schema_version": "route.v1",
        "request_context": _route_request_context(source_channel="dashboard"),
        "input": {"prompt": "Record this fact."},
        "source_metadata": {
            "channel": "dashboard",
            "identity": "dashboard:owner",
            "tool_name": "ingest",
            "dashboard_message_id": str(message_id),
        },
    }

    processing_claim_id = uuid.uuid4()
    request_id = uuid.UUID(_route_request_context(source_channel="dashboard")["request_id"])

    async def _recover_once(*_args, dispatch_fn, **_kwargs):
        await dispatch_fn(
            row_id=row_id,
            route_envelope=envelope,
            processing_claim_id=processing_claim_id,
            recovery_from_processing=True,
        )
        return 1

    with (
        patch("butlers.switchboard_wiring.route_inbox_recovery_sweep", _recover_once),
        patch(
            "butlers.core.route_inbox.route_inbox_renew_processing_claim",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "butlers.switchboard_wiring.reconcile_route_recovery",
            new_callable=AsyncMock,
            return_value=_dashboard_turn_result(
                outcome,
                message_id=message_id,
                request_id=request_id,
                inbox_id=row_id,
            ),
        ) as reconcile,
        patch(
            "butlers.switchboard_wiring.route_inbox_mark_errored",
            new_callable=AsyncMock,
            return_value=True,
        ) as errored,
        patch(
            "butlers.switchboard_wiring.route_inbox_mark_processed",
            new_callable=AsyncMock,
            return_value=True,
        ) as processed,
    ):
        await recover_route_inbox(daemon, pool)

    trigger.assert_not_awaited()
    reconcile.assert_awaited_once_with(
        pool,
        message_id=message_id,
        request_id=request_id,
        route_inbox_id=row_id,
    )
    errored.assert_awaited_once_with(
        pool,
        row_id,
        "Dashboard recovery could not prove the prior runtime stopped; "
        f"automatic replay was suppressed ({outcome}).",
        processing_claim_id=processing_claim_id,
    )
    processed.assert_not_awaited()


async def test_dashboard_accepted_recovery_replays_before_runtime_handoff() -> None:
    """A dashboard row accepted before dispatch may safely resume its original runtime."""
    from types import SimpleNamespace

    from butlers.switchboard_wiring import recover_route_inbox

    pool = AsyncMock()
    message_id = uuid.uuid4()
    row_id = uuid.uuid4()
    processing_claim_id = uuid.uuid4()
    trigger = _make_trigger_mock()
    daemon = SimpleNamespace(
        config=SimpleNamespace(name="health"),
        spawner=SimpleNamespace(trigger=trigger),
    )
    envelope = {
        "schema_version": "route.v1",
        "request_context": _route_request_context(source_channel="dashboard"),
        "input": {"prompt": "Resume this accepted dashboard route."},
        "source_metadata": {
            "channel": "dashboard",
            "identity": "dashboard:owner",
            "tool_name": "ingest",
            "dashboard_message_id": str(message_id),
        },
    }

    async def _recover_once(*_args, dispatch_fn, **_kwargs):
        await dispatch_fn(
            row_id=row_id,
            route_envelope=envelope,
            processing_claim_id=processing_claim_id,
            recovery_from_processing=False,
        )
        return 1

    with (
        patch("butlers.switchboard_wiring.route_inbox_recovery_sweep", _recover_once),
        patch(
            "butlers.core.route_inbox.route_inbox_renew_processing_claim",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "butlers.switchboard_wiring.reconcile_route_recovery",
            new_callable=AsyncMock,
        ) as reconcile,
        patch(
            "butlers.switchboard_wiring.route_inbox_mark_processed",
            new_callable=AsyncMock,
            return_value=True,
        ) as processed,
    ):
        await recover_route_inbox(daemon, pool)

    trigger.assert_awaited_once()
    assert trigger.await_args.kwargs["dashboard_turn_id"] == message_id
    assert trigger.await_args.kwargs["trigger_source"] == "route"
    reconcile.assert_not_awaited()
    processed.assert_awaited_once_with(
        pool,
        row_id,
        trigger.return_value.session_id,
        processing_claim_id=processing_claim_id,
    )


async def test_recovery_reconciliation_keeps_claim_fenced_before_spawn() -> None:
    """A recovery takeover while reconciling cannot start the displaced runtime."""
    from types import SimpleNamespace

    from butlers.core.route_inbox import RouteInboxLeaseLost
    from butlers.switchboard_wiring import recover_route_inbox

    class _ReclaimedLeaseHeartbeat:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.lease_lost = asyncio.Event()

        async def __aenter__(self) -> asyncio.Event:
            self.entered.set()
            return self.lease_lost

        async def __aexit__(self, *_args: object) -> bool:
            return False

    pool = AsyncMock()
    message_id = uuid.uuid4()
    row_id = uuid.uuid4()
    request_id = uuid.UUID(_route_request_context(source_channel="dashboard")["request_id"])
    processing_claim_id = uuid.uuid4()
    trigger = _make_trigger_mock()
    daemon = SimpleNamespace(
        config=SimpleNamespace(name="health"),
        spawner=SimpleNamespace(trigger=trigger),
    )
    envelope = {
        "schema_version": "route.v1",
        "request_context": _route_request_context(source_channel="dashboard"),
        "input": {"prompt": "Do not replay a reclaimed recovery."},
        "source_metadata": {
            "channel": "dashboard",
            "identity": "dashboard:owner",
            "tool_name": "ingest",
            "dashboard_message_id": str(message_id),
        },
    }
    heartbeat = _ReclaimedLeaseHeartbeat()
    reconciliation_saw_heartbeat = False

    async def reconcile_during_recovery(
        _pool: object,
        **_kwargs: object,
    ) -> DashboardTurnResult:
        nonlocal reconciliation_saw_heartbeat
        reconciliation_saw_heartbeat = heartbeat.entered.is_set()
        if reconciliation_saw_heartbeat:
            heartbeat.lease_lost.set()
        return _dashboard_turn_result("active", message_id=message_id, request_id=request_id)

    async def wait_while_reclaiming(*args: object) -> object:
        if len(args) == 2:
            lease_lost, invocation = args
        else:
            _pool, _row_id, _claim_id, lease_lost, invocation = args
        assert isinstance(lease_lost, asyncio.Event)
        assert callable(invocation)
        if lease_lost.is_set():
            raise RouteInboxLeaseLost("reclaimed during recovery reconciliation")
        return await invocation()

    async def recover_once(*_args: object, dispatch_fn: object, **_kwargs: object) -> int:
        assert callable(dispatch_fn)
        await dispatch_fn(
            row_id=row_id,
            route_envelope=envelope,
            processing_claim_id=processing_claim_id,
            recovery_from_processing=True,
        )
        return 1

    mark_processed = AsyncMock(return_value=True)
    mark_errored = AsyncMock(return_value=True)
    with (
        patch("butlers.switchboard_wiring.route_inbox_recovery_sweep", recover_once),
        patch("butlers.switchboard_wiring.reconcile_route_recovery", reconcile_during_recovery),
        patch(
            "butlers.switchboard_wiring.route_inbox_processing_lease_heartbeat",
            return_value=heartbeat,
        ),
        patch("butlers.switchboard_wiring.route_inbox_wait_while_claimed", wait_while_reclaiming),
        patch("butlers.switchboard_wiring.route_inbox_mark_processed", mark_processed),
        patch("butlers.switchboard_wiring.route_inbox_mark_errored", mark_errored),
    ):
        await recover_route_inbox(daemon, pool)

    assert reconciliation_saw_heartbeat is True
    trigger.assert_not_awaited()
    mark_processed.assert_not_awaited()
    mark_errored.assert_not_awaited()


async def test_failure_recording_and_dedup(tmp_path: Path) -> None:
    """Trigger failures stored in route_inbox; success stored; duplicate request_ids deduped."""
    patches = _patch_infra("health")
    butler_dir = _make_butler_toml(tmp_path, butler_name="health")
    daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
    assert route_execute_fn is not None

    # Failure path
    daemon.spawner.trigger = AsyncMock(side_effect=RuntimeError("spawner crash"))
    error_recorded = asyncio.Event()

    async def _record_error(*_args: Any, **_kwargs: Any) -> None:
        error_recorded.set()

    mock_errored = AsyncMock(side_effect=_record_error)
    with (
        patch(
            "butlers.core_tools._routing.route_inbox_insert",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_claim_processing",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch("butlers.core_tools._routing.route_inbox_mark_errored", mock_errored),
    ):
        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
        )
        assert result["status"] == "accepted"
        await asyncio.wait_for(error_recorded.wait(), timeout=1)
    mock_errored.assert_awaited_once()

    # Dedup: existing session → skip insert, return accepted with dedup=True
    existing_session_id = uuid.uuid4()
    patches["mock_pool"].fetchval.return_value = existing_session_id
    mock_insert = AsyncMock(return_value=uuid.uuid4())
    with patch("butlers.core_tools._routing.route_inbox_insert", mock_insert):
        result2 = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
        )
    assert result2["status"] == "accepted"
    assert result2.get("dedup") is True
    mock_insert.assert_not_awaited()


async def test_lease_loss_does_not_settle_a_route_inbox_row(tmp_path: Path) -> None:
    """A worker that loses its lease leaves terminal settlement to the current owner."""

    patches = _patch_infra("health")
    butler_dir = _make_butler_toml(tmp_path, butler_name="health")
    daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
    assert route_execute_fn is not None

    from butlers.core.route_inbox import RouteInboxLeaseLost

    lease_loss_observed = asyncio.Event()

    async def _lose_lease(*args: Any) -> None:
        # A loss known before the invocation starts must not construct a
        # trigger coroutine. The production helper receives a factory for
        # precisely that reason.
        if len(args) == 2:
            _lease_lost, invocation_factory = args
        else:
            _pool, _row_id, _claim_id, _lease_lost, invocation_factory = args
        assert isinstance(_lease_lost, asyncio.Event)
        assert callable(invocation_factory)
        lease_loss_observed.set()
        raise RouteInboxLeaseLost("test lease loss")

    mock_errored = AsyncMock()
    with (
        patch(
            "butlers.core_tools._routing.route_inbox_insert",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_claim_processing",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch("butlers.core_tools._routing.route_inbox_wait_while_claimed", _lose_lease),
        patch("butlers.core_tools._routing.route_inbox_mark_errored", mock_errored),
    ):
        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
        )
        assert result["status"] == "accepted"
        await asyncio.wait_for(lease_loss_observed.wait(), timeout=1)
        for _ in range(10):
            await asyncio.sleep(0)

    mock_errored.assert_not_awaited()


async def test_lease_loss_after_runtime_result_does_not_settle_a_route_inbox_row(
    tmp_path: Path,
) -> None:
    """A heartbeat loss racing a completed runtime still relinquishes the queue row."""

    patches = _patch_infra("health")
    butler_dir = _make_butler_toml(tmp_path, butler_name="health")
    daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
    assert route_execute_fn is not None

    class _LateLossHeartbeat:
        def __init__(self) -> None:
            self.lease_lost = asyncio.Event()

        async def __aenter__(self) -> asyncio.Event:
            return self.lease_lost

        async def __aexit__(self, *_args: object) -> bool:
            return False

    heartbeat = _LateLossHeartbeat()
    trigger = _make_trigger_mock()
    daemon.spawner.trigger = trigger

    async def complete_then_lose_lease(*args: object) -> object:
        _pool, _row_id, _claim_id, lease_lost, invocation_factory = args
        assert lease_lost is heartbeat.lease_lost
        assert callable(invocation_factory)
        result = await invocation_factory()
        lease_lost.set()
        return result

    mark_processed = AsyncMock(return_value=True)
    mark_errored = AsyncMock(return_value=True)
    with (
        patch(
            "butlers.core_tools._routing.route_inbox_insert",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_claim_processing",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_processing_lease_heartbeat",
            return_value=heartbeat,
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_wait_while_claimed", complete_then_lose_lease
        ),
        patch("butlers.core_tools._routing.route_inbox_mark_processed", mark_processed),
        patch("butlers.core_tools._routing.route_inbox_mark_errored", mark_errored),
    ):
        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Do not settle after a late lease loss."},
        )
        assert result["status"] == "accepted"
        tasks = tuple(daemon._route_inbox_tasks)
        assert len(tasks) == 1
        await asyncio.wait_for(tasks[0], timeout=1)

    trigger.assert_awaited_once()
    mark_processed.assert_not_awaited()
    mark_errored.assert_not_awaited()


async def test_crash_recovery_on_startup(tmp_path: Path) -> None:
    """_recover_route_inbox called for non-staffer; not called for staffer type."""
    patches = _patch_infra("health")
    del patches["recover_route_inbox"]

    butler_dir = _make_butler_toml(tmp_path, butler_name="health")
    mock_mcp = MagicMock()
    mock_mcp.tool = lambda *a, **kw: lambda fn: fn

    recovery_called = False

    async def mock_recover(self_daemon, pool):
        nonlocal recovery_called
        recovery_called = True

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
        patches["init_telemetry"],
        patches["sync_schedules"],
        patch("butlers.lifecycle.FastMCP", return_value=mock_mcp),
        patch("butlers.lifecycle.Spawner", return_value=patches["mock_spawner"]),
        patches["get_adapter"],
        patches["shutil_which"],
        patches["start_mcp_server"],
        patches["connect_switchboard"],
        patch.object(ButlerDaemon, "_recover_route_inbox", mock_recover),
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()
        await asyncio.sleep(0)

    assert recovery_called, "_recover_route_inbox was not called on startup"

    # Staffer does NOT schedule recovery
    patches2 = _patch_infra("infratool")
    butler_dir2 = _make_butler_toml(
        tmp_path, butler_name="infratool", port=9302, butler_type="staffer"
    )
    mock_mcp2 = MagicMock()
    mock_mcp2.tool = lambda *a, **kw: lambda fn: fn
    with (
        patches2["db_from_env"],
        patches2["run_migrations"],
        patches2["validate_credentials"],
        patches2["validate_module_credentials"],
        patches2["init_telemetry"],
        patches2["sync_schedules"],
        patch("butlers.lifecycle.FastMCP", return_value=mock_mcp2),
        patch("butlers.lifecycle.Spawner", return_value=patches2["mock_spawner"]),
        patches2["get_adapter"],
        patches2["shutil_which"],
        patches2["start_mcp_server"],
        patches2["connect_switchboard"],
        patches2["recover_route_inbox"],
        patch.object(ButlerDaemon, "_wire_pipelines"),
    ):
        daemon2 = ButlerDaemon(butler_dir2)
        await daemon2.start()
    assert daemon2._route_inbox_recovery_task is None


# ---------------------------------------------------------------------------
# Complexity coercion on the route.execute accept path (bu-kereb)
#
# The accept path normalizes ``input.complexity`` via the shared
# ``coerce_complexity_tier(strict=False)`` idiom (converging with the schedules
# API read path, PR #3049, and the scheduler dispatch path, bu-lq7m4) rather
# than a raw ``Complexity()`` construction, then forwards the resulting
# ``Complexity`` enum to ``spawner.trigger(complexity=...)``.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "envelope_complexity, expected",
    [
        ("reasoning", Complexity.REASONING),
        ("workhorse", Complexity.WORKHORSE),
        ("cheap", Complexity.CHEAP),
        ("specialty", Complexity.SPECIALTY),
        ("local", Complexity.LOCAL),
        ("legacy", Complexity.LEGACY),
        (None, Complexity.WORKHORSE),  # missing key -> canonical default
    ],
)
async def test_accept_path_coerces_complexity_and_plumbs_enum_to_trigger(
    tmp_path: Path, envelope_complexity: str | None, expected: Complexity
) -> None:
    """Every canonical envelope complexity resolves to its ``Complexity`` member
    and is forwarded to ``spawner.trigger(complexity=...)`` as the enum (not the
    raw string). Missing complexity defaults to WORKHORSE.

    Only canonical tiers are exercised here because the envelope validator
    (``RouteInputV1``) rejects retired vocabulary before it can reach the accept
    path — see ``test_envelope_validator_rejects_retired_complexity_tiers``.
    """
    patches = _patch_infra("health")
    butler_dir = _make_butler_toml(tmp_path, butler_name="health")
    daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
    assert route_execute_fn is not None

    input_payload: dict[str, Any] = {"prompt": "Run health check."}
    if envelope_complexity is not None:
        input_payload["complexity"] = envelope_complexity

    trigger_mock = _make_trigger_mock()
    daemon.spawner.trigger = trigger_mock
    with (
        patch(
            "butlers.core_tools._routing.route_inbox_insert",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_claim_processing",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_mark_processed",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input=input_payload,
        )
        await asyncio.sleep(0.05)

    trigger_mock.assert_awaited()
    forwarded = trigger_mock.call_args.kwargs["complexity"]
    assert forwarded == expected
    assert isinstance(forwarded, Complexity)


@pytest.mark.parametrize(
    "retired_tier",
    ["trivial", "medium", "high", "extra_high", "discretion", "self_healing"],
)
def test_envelope_validator_rejects_retired_complexity_tiers(retired_tier: str) -> None:
    """The route envelope validator refuses every retired pre-core_092 tier, so
    retired vocabulary can never reach the accept path's coerce call.

    This is why delegating to ``coerce_complexity_tier(strict=False)`` on this
    hot path does NOT emit a per-envelope deprecation warning in normal
    operation: the retired-tier remap branch is unreachable through a parsed
    envelope. The coerce call is defense-in-depth that stays correct if this
    upstream guard is ever loosened.
    """
    envelope = {
        "schema_version": "route.v1",
        "request_context": _route_request_context(),
        "input": {"prompt": "hi", "complexity": retired_tier},
    }
    with pytest.raises(Exception, match="is not valid"):
        parse_route_envelope(envelope)


@pytest.mark.parametrize(
    "value, expected",
    [
        # Retired pre-core_092 vocabulary -> canonical successor (the contract
        # the accept path converges onto; kept here as a guard in case the
        # envelope validator is ever relaxed to admit legacy aliases).
        ("trivial", Complexity.CHEAP),
        ("medium", Complexity.WORKHORSE),
        ("high", Complexity.REASONING),
        ("extra_high", Complexity.REASONING),
        ("discretion", Complexity.SPECIALTY),
        ("self_healing", Complexity.SPECIALTY),
        # Canonical values pass through unchanged.
        ("reasoning", Complexity.REASONING),
        ("workhorse", Complexity.WORKHORSE),
        # Missing / junk degrade to WORKHORSE (fail-open on the routing hot path).
        (None, Complexity.WORKHORSE),
        ("", Complexity.WORKHORSE),
        ("bogus-tier", Complexity.WORKHORSE),
    ],
)
def test_accept_path_complexity_coercion_contract(value: str | None, expected: Complexity) -> None:
    """Pin the ``coerce_complexity_tier(strict=False)`` contract the accept path
    now delegates to: retired -> canonical, canonical pass-through, junk/None ->
    WORKHORSE, always returning a ``Complexity`` enum."""
    result = coerce_complexity_tier(value, strict=False)
    assert result == expected
    assert isinstance(result, Complexity)
