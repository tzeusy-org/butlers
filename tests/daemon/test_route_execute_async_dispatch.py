"""Tests for route.execute accept-then-process async dispatch (butlers-963.6).

Verifies:
1. Accept phase: route.execute returns {"status": "accepted"} without awaiting trigger
2. Persist + background dispatch: the route_inbox row is written; trigger() runs in background
3. Failure/success recording: errors stored in route_inbox
4. Crash recovery: route_inbox recovery scheduled on startup (not for staffer)
5. Complexity plumbing: complexity forwarded to spawner.trigger()

Tests that take ``booted_daemon`` boot a real daemon and call route.execute over MCP;
the route inbox, lease, dashboard-turn control plane, recovery sweep and spawner are
the production ones, and their outcomes are read back from the clone. The spawner's
``trigger`` is wrapped by a pass-through recorder, so what route.execute hands the
spawner is observed at that boundary. Failures the database will not produce on its
own (the spawner raising, catalog resolution refusing, a recovery worker taking the
lease over) are injected at the seam named in each test; nothing in the lifecycle
is patched. The recovery-sweep unit tests below them drive ``recover_route_inbox``
with fakes and boot nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.api import conversations
from butlers.core import dashboard_turns
from butlers.core.dashboard_turns import DashboardTurnResult
from butlers.core.model_routing import (
    Complexity,
    coerce_complexity_tier,
)
from butlers.core.route_inbox import route_inbox_insert
from butlers.core.routing_context import _routing_ctx_var
from butlers.testing.daemon import write_butler_config
from butlers.testing.db import open_pool
from butlers.tools.switchboard.routing.contracts import parse_route_envelope

pytestmark = pytest.mark.unit


def _booted(test: Callable[..., Any]) -> Callable[..., Any]:
    """Run *test* in the integration lane, on the session loop the kit's fixtures use."""
    return pytest.mark.integration(pytest.mark.asyncio(loop_scope="session")(test))


_REQUEST_IDS = (f"018f6f4e-5b3b-7b2d-9c2f-{n:012x}" for n in itertools.count(0xD0))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _live_context(**overrides: Any) -> dict[str, Any]:
    """A request context with a fresh request_id: the route inbox is idempotent per id."""
    return {**_route_request_context(), "request_id": next(_REQUEST_IDS), **overrides}


async def _boot(booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs: Any):
    """Boot a registered butler whose spawner records each trigger.

    ``booted.triggers`` gets ``(kwargs, routing context)`` per call. ``booted.before``
    may be set to an async hook that runs before the real trigger, and
    ``booted.after`` to one that runs after it returns.
    """
    booted = await booted_daemon(write_butler_config(tmp_path, "general"), registered=True)
    spawner = booted.daemon.spawner
    real_trigger = spawner.trigger
    booted.triggers = []
    booted.before = None
    booted.after = None

    async def recording_trigger(**trigger_kwargs: Any) -> Any:
        booted.triggers.append((trigger_kwargs, dict(_routing_ctx_var.get() or {})))
        if booted.before is not None:
            await booted.before(trigger_kwargs)
        result = await real_trigger(**trigger_kwargs)
        if booted.after is not None:
            await booted.after(trigger_kwargs)
        return result

    monkeypatch.setattr(spawner, "trigger", recording_trigger)
    return booted


async def _route(booted, request_context: dict[str, Any], **payload: Any) -> dict[str, Any]:
    payload.setdefault("input", {"prompt": "Run health check."})
    return await booted.call_tool(
        "route.execute",
        {"schema_version": "route.v1", "request_context": request_context, **payload},
    )


async def _drain(booted) -> None:
    """Wait for every background route task the daemon started, including follow-ons."""
    while booted.daemon._route_inbox_tasks:
        await asyncio.gather(*tuple(booted.daemon._route_inbox_tasks))
        # Finished tasks leave the set from a done-callback, which needs a loop turn;
        # awaiting already-done tasks never yields, so without this the loop spins.
        await asyncio.sleep(0)


async def _inbox_rows(booted) -> list[dict[str, Any]]:
    rows = await booted.daemon.db.pool.fetch(
        """
        SELECT id, lifecycle_state, error, session_id, processing_claim_id, route_envelope
        FROM route_inbox ORDER BY received_at
        """
    )
    return [dict(row) for row in rows]


async def _take_over_lease(booted) -> uuid.UUID:
    """Model a recovery worker replacing every processing claim; return its claim."""
    takeover = uuid.uuid4()
    await booted.daemon.db.pool.execute(
        "UPDATE route_inbox SET processing_claim_id = $1, processing_claimed_at = now() "
        "WHERE lifecycle_state = 'processing'",
        takeover,
    )
    return takeover


async def _make_unprocessed(booted, row_id: uuid.UUID) -> None:
    """Return a row to ``accepted`` past the recovery grace, as a crash would leave it."""
    await booted.daemon.db.pool.execute(
        """
        UPDATE route_inbox
        SET lifecycle_state = 'accepted', received_at = now() - interval '1 hour',
            processing_claim_id = NULL, processing_claimed_at = NULL,
            processed_at = NULL, session_id = NULL, error = NULL
        WHERE id = $1
        """,
        row_id,
    )


async def _recover(booted) -> None:
    """Run the startup recovery sweep the daemon schedules, then its dispatches."""
    await booted.daemon._recover_route_inbox(booted.daemon.db.pool)
    await _drain(booted)


async def _open_dashboard_turn(booted, prompt: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a dashboard conversation, its user message and the durable turn.

    The dashboard API owns these rows, so they are written through the clone's owner.
    """
    admin = await open_pool(booted.admin_url)
    try:
        conversation = await conversations.conversation_create(
            admin, butler_name="general", first_message=prompt
        )
        message_id = uuid.uuid4()
        await conversations.message_create_idempotent(
            admin,
            message_id=message_id,
            conversation_id=conversation["id"],
            role="user",
            content=prompt,
        )
        turn = await dashboard_turns.open_turn(
            admin, message_id=message_id, conversation_id=conversation["id"]
        )
    finally:
        await admin.close()
    assert turn.message_id == message_id
    return conversation["id"], message_id


async def _dashboard_turn(booted, message_id: uuid.UUID) -> dict[str, Any]:
    """Read the durable turn; only the control-plane functions expose it to butlers."""
    admin = await open_pool(booted.admin_url)
    try:
        row = await admin.fetchrow(
            """
            SELECT request_id, target_butler, target_kind, route_inbox_id
            FROM public.dashboard_conversation_turns WHERE message_id = $1
            """,
            message_id,
        )
    finally:
        await admin.close()
    assert row is not None
    return dict(row)


def _capture_resolution(
    monkeypatch: pytest.MonkeyPatch, *, refuse: bool
) -> list[dict[str, Any] | None]:
    """Record the routing context catalog resolution sees.

    With *refuse*, resolution then finds no catalog entry, as it does when no entry
    may serve the channel's sensitivity; the seeded catalog always has one.
    """
    import butlers.core.spawner as spawner_module
    from butlers.core.spawner_env import _capture_pipeline_routing_context

    real_resolve = spawner_module.resolve_model_with_effective_tier
    seen: list[dict[str, Any] | None] = []

    async def resolve(*args: Any, **kwargs: Any) -> Any:
        seen.append(_capture_pipeline_routing_context())
        return None if refuse else await real_resolve(*args, **kwargs)

    monkeypatch.setattr(spawner_module, "resolve_model_with_effective_tier", resolve)
    return seen


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@_booted
async def test_accept_phase_and_background_dispatch(
    booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accepted before the session runs: the inbox row is written, then trigger runs."""
    booted = await _boot(booted_daemon, tmp_path, monkeypatch)
    release = asyncio.Event()

    async def hold(_kwargs: dict[str, Any]) -> None:
        await release.wait()

    booted.before = hold
    context = _live_context()

    result = await _route(booted, context)

    # The response came back while the background session is still held.
    assert result["status"] == "accepted"
    assert result["schema_version"] == "route_response.v1"
    assert booted.runtime.invocations == []
    (row,) = await _inbox_rows(booted)
    assert result["inbox_id"] == str(row["id"])
    assert row["route_envelope"]["schema_version"] == "route.v1"
    assert row["route_envelope"]["input"]["prompt"] == "Run health check."

    release.set()
    await _drain(booted)

    ((trigger, _),) = booted.triggers
    assert trigger["trigger_source"] == "route"
    assert trigger["request_id"] == context["request_id"]
    (row,) = await _inbox_rows(booted)
    assert row["lifecycle_state"] == "processed"
    assert len(booted.runtime.invocations) == 1


@_booted
async def test_real_route_worker_preserves_private_channel_into_fail_closed_resolution(
    booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A production WhatsApp route keeps its private channel at catalog resolution."""
    booted = await _boot(booted_daemon, tmp_path, monkeypatch)
    seen = _capture_resolution(monkeypatch, refuse=True)

    result = await _route(
        booted,
        _live_context(source_channel="whatsapp_user_client"),
        input={"prompt": "Private synthetic fixture."},
    )
    await _drain(booted)

    assert result["status"] == "accepted"
    assert booted.runtime.invocations == []
    (routing_context,) = seen
    assert routing_context is not None
    assert routing_context["request_context"]["source_channel"] == "whatsapp_user_client"
    (row,) = await _inbox_rows(booted)
    assert row["lifecycle_state"] == "errored"
    assert row["error"] == "route runtime returned unsuccessful result (ModelResolutionError)"


@_booted
async def test_dashboard_route_acceptance_atomically_claims_and_enqueues_before_spawning(
    booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A target cannot schedule a dashboard runtime outside the Stop transaction."""
    booted = await _boot(booted_daemon, tmp_path, monkeypatch)
    conversation_id, message_id = await _open_dashboard_turn(booted, "Record this fact.")
    turn_at_trigger: list[dict[str, Any]] = []

    async def read_turn(_kwargs: dict[str, Any]) -> None:
        turn_at_trigger.append(await _dashboard_turn(booted, message_id))

    booted.before = read_turn
    context = _live_context(source_channel="dashboard", source_thread_identity=str(conversation_id))

    result = await _route(
        booted,
        context,
        input={"prompt": "Record this fact."},
        source_metadata={
            "channel": "dashboard",
            "identity": "dashboard:owner",
            "tool_name": "ingest",
            "dashboard_message_id": str(message_id),
        },
    )
    await _drain(booted)

    assert result["status"] == "accepted"
    (row,) = await _inbox_rows(booted)
    assert result["inbox_id"] == str(row["id"])
    assert row["route_envelope"]["source_metadata"]["dashboard_message_id"] == str(message_id)
    # The turn was claimed for this butler and marked enqueued on this row before
    # the spawner was ever called.
    claimed = {
        "request_id": uuid.UUID(context["request_id"]),
        "target_butler": "general",
        "target_kind": "route",
        "route_inbox_id": row["id"],
    }
    assert turn_at_trigger == [claimed]
    ((trigger, _),) = booted.triggers
    assert trigger["dashboard_turn_id"] == message_id


@pytest.mark.parametrize("outcome", ["ambiguous", "active", "cancelling"])
async def test_dashboard_processing_recovery_never_replays_a_predecessor(
    outcome: str,
) -> None:
    """Every non-terminal recovery result fails closed rather than replaying."""
    from types import SimpleNamespace

    from butlers.switchboard_wiring import recover_route_inbox

    pool = AsyncMock()
    message_id = uuid.uuid4()
    row_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
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


async def test_recovery_restores_structured_conceptual_message_context() -> None:
    """Spec: REQ-conversation-decomposition-001, REQ-entity-identity-001."""
    from types import SimpleNamespace

    from butlers.core.routing_context import _routing_ctx_var
    from butlers.switchboard_wiring import recover_route_inbox

    pool = AsyncMock()
    row_id = uuid.uuid4()
    processing_claim_id = uuid.uuid4()
    conceptual_message = {
        "signal_type": "health",
        "excerpts": [
            {
                "message_id": "m1",
                "sender_entity_id": "11111111-1111-1111-1111-111111111111",
            }
        ],
        "confidence": "HIGH",
    }
    captured_context: list[Any] = []
    trigger_result = SimpleNamespace(success=True, error=None, session_id="session-1")

    async def _trigger(**_kwargs: Any) -> Any:
        captured_context.append(_routing_ctx_var.get())
        return trigger_result

    daemon = SimpleNamespace(
        config=SimpleNamespace(name="health"),
        spawner=SimpleNamespace(trigger=AsyncMock(side_effect=_trigger)),
    )
    envelope = {
        "schema_version": "route.v1",
        "request_context": _route_request_context(source_channel="whatsapp_user_client"),
        "input": {
            "prompt": "Store facts from this excerpt.",
            "context": {"conceptual_message": conceptual_message},
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

    _routing_ctx_var.set(None)
    recovery_span = MagicMock()
    span_context = MagicMock()
    span_context.__enter__.return_value = recovery_span
    span_context.__exit__.return_value = False
    tracer = MagicMock()
    tracer.start_as_current_span.return_value = span_context
    with (
        patch("butlers.switchboard_wiring.route_inbox_recovery_sweep", _recover_once),
        patch(
            "butlers.core.route_inbox.route_inbox_renew_processing_claim",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "butlers.switchboard_wiring.route_inbox_mark_processed",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("butlers.switchboard_wiring.trace.get_tracer", return_value=tracer),
    ):
        await recover_route_inbox(daemon, pool)

    assert captured_context == [
        {
            "request_context": {"source_channel": "whatsapp_user_client"},
            "conceptual_message": conceptual_message,
        }
    ]
    observability = repr(recovery_span.set_attribute.call_args_list)
    assert envelope["request_context"]["request_id"] not in observability
    assert str(row_id) not in observability


@_booted
async def test_recovery_preserves_private_channel_into_fail_closed_resolution(
    booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crash recovery keeps the private channel when catalog resolution refuses."""
    booted = await _boot(booted_daemon, tmp_path, monkeypatch)
    row_id = await route_inbox_insert(
        booted.daemon.db.pool,
        route_envelope={
            "schema_version": "route.v1",
            "request_context": _live_context(source_channel="telegram_user_client"),
            "input": {"prompt": "Private recovery fixture."},
        },
    )
    await _make_unprocessed(booted, row_id)
    seen = _capture_resolution(monkeypatch, refuse=True)

    await _recover(booted)

    assert booted.runtime.invocations == []
    (routing_context,) = seen
    assert routing_context is not None
    assert routing_context["request_context"]["source_channel"] == "telegram_user_client"
    (row,) = await _inbox_rows(booted)
    assert row["lifecycle_state"] == "errored"
    assert row["error"] == "route runtime returned unsuccessful result (ModelResolutionError)"


@_booted
async def test_failure_recording_and_dedup(
    booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trigger failures stored in route_inbox; success stored; duplicate request_ids deduped."""
    booted = await _boot(booted_daemon, tmp_path, monkeypatch)

    # Failure path: the spawner itself raising is recorded as a bounded failure class.
    async def crash(_kwargs: dict[str, Any]) -> None:
        raise RuntimeError("spawner crash")

    booted.before = crash
    failed = await _route(booted, _live_context())
    await _drain(booted)
    assert failed["status"] == "accepted"
    (errored,) = await _inbox_rows(booted)
    assert (errored["lifecycle_state"], errored["error"]) == (
        "errored",
        "route runtime failed (RuntimeError)",
    )

    # Success, then a duplicate of it: the second skips the inbox and names the session.
    booted.before = None
    context = _live_context()
    await _route(booted, context)
    await _drain(booted)
    processed = (await _inbox_rows(booted))[1]
    assert processed["lifecycle_state"] == "processed"

    duplicate = await _route(booted, context)

    assert duplicate["status"] == "accepted"
    assert duplicate.get("dedup") is True
    assert duplicate["existing_session_id"] == str(processed["session_id"])
    assert len(await _inbox_rows(booted)) == 2
    assert len(booted.triggers) == 2


@_booted
async def test_lease_loss_does_not_settle_a_route_inbox_row(
    booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker that loses its lease leaves terminal settlement to the current owner."""
    import butlers.core_tools._routing as routing

    booted = await _boot(booted_daemon, tmp_path, monkeypatch)
    real_heartbeat = routing.route_inbox_processing_lease_heartbeat
    takeovers: list[uuid.UUID] = []

    def heartbeat_then_takeover(*args: Any, **kwargs: Any) -> Any:
        @contextlib.asynccontextmanager
        async def lease():
            async with real_heartbeat(*args, **kwargs) as lease_lost:
                # The worker's own fence passed; a recovery worker now takes over
                # before the invocation starts.
                takeovers.append(await _take_over_lease(booted))
                yield lease_lost

        return lease()

    monkeypatch.setattr(routing, "route_inbox_processing_lease_heartbeat", heartbeat_then_takeover)

    result = await _route(booted, _live_context())
    await _drain(booted)

    assert result["status"] == "accepted"
    assert booted.triggers == []
    assert booted.runtime.invocations == []
    (row,) = await _inbox_rows(booted)
    assert (row["lifecycle_state"], row["error"]) == ("processing", None)
    assert [row["processing_claim_id"]] == takeovers


@_booted
async def test_lease_loss_after_runtime_result_does_not_settle_a_route_inbox_row(
    booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lease lost while the runtime finished still relinquishes the queue row."""
    booted = await _boot(booted_daemon, tmp_path, monkeypatch)
    takeovers: list[uuid.UUID] = []

    async def take_over(_kwargs: dict[str, Any]) -> None:
        takeovers.append(await _take_over_lease(booted))

    booted.after = take_over

    result = await _route(booted, _live_context(), input={"prompt": "Do not settle late."})
    await _drain(booted)

    assert result["status"] == "accepted"
    assert len(booted.runtime.invocations) == 1
    (row,) = await _inbox_rows(booted)
    assert (row["lifecycle_state"], row["error"], row["session_id"]) == ("processing", None, None)
    assert [row["processing_claim_id"]] == takeovers


@_booted
async def test_crash_recovery_on_startup(
    booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup schedules route_inbox recovery for non-staffers, which replays crashed rows."""
    booted = await _boot(booted_daemon, tmp_path, monkeypatch)
    assert booted.daemon._route_inbox_recovery_task is not None
    await booted.daemon._route_inbox_recovery_task
    row_id = await route_inbox_insert(
        booted.daemon.db.pool,
        route_envelope={
            "schema_version": "route.v1",
            "request_context": _live_context(),
            "input": {"prompt": "Left behind by a crash."},
        },
    )
    await _make_unprocessed(booted, row_id)

    await _recover(booted)

    (invocation,) = booted.runtime.invocations
    assert "Left behind by a crash." in invocation["prompt"]
    (row,) = await _inbox_rows(booted)
    assert (row["id"], row["lifecycle_state"]) == (row_id, "processed")

    # Staffers have their own durable routing and never schedule this recovery.
    staffer_dir = write_butler_config(tmp_path / "staffer", "general")
    toml = (staffer_dir / "butler.toml").read_text()
    (staffer_dir / "butler.toml").write_text(
        toml.replace("port = 0\n", 'port = 0\ntype = "staffer"\n')
    )
    staffer = await booted_daemon(staffer_dir)
    assert staffer.daemon._route_inbox_recovery_task is None


# ---------------------------------------------------------------------------
# Complexity coercion on the route.execute accept path (bu-kereb)
#
# The accept path normalizes ``input.complexity`` via the shared
# ``coerce_complexity_tier(strict=False)`` idiom (converging with the schedules
# API read path, PR #3049, and the scheduler dispatch path, bu-lq7m4) rather
# than a raw ``Complexity()`` construction, then forwards the resulting
# ``Complexity`` enum to ``spawner.trigger(complexity=...)``.
# ---------------------------------------------------------------------------


@_booted
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
    booted_daemon,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    envelope_complexity: str | None,
    expected: Complexity,
) -> None:
    """Every canonical envelope complexity resolves to its ``Complexity`` member
    and is forwarded to ``spawner.trigger(complexity=...)`` as the enum (not the
    raw string). Missing complexity defaults to WORKHORSE.

    Only canonical tiers are exercised here because the envelope validator
    (``RouteInputV1``) rejects retired vocabulary before it can reach the accept
    path — see ``test_envelope_validator_rejects_retired_complexity_tiers``.
    """
    booted = await _boot(booted_daemon, tmp_path, monkeypatch)
    input_payload: dict[str, Any] = {"prompt": "Run health check."}
    if envelope_complexity is not None:
        input_payload["complexity"] = envelope_complexity

    await _route(booted, _live_context(), input=input_payload)
    await _drain(booted)

    ((trigger, _),) = booted.triggers
    forwarded = trigger["complexity"]
    assert forwarded == expected
    assert isinstance(forwarded, Complexity)


async def test_malformed_conceptual_recovery_log_omits_row_and_request_uuids() -> None:
    """Malformed persisted conceptual routes stay content-blind before validation."""
    from types import SimpleNamespace

    from butlers.switchboard_wiring import recover_route_inbox

    pool = AsyncMock()
    row_id = uuid.UUID("44444444-4444-4444-8444-444444444444")
    request_id = "018f6f4e-5b3b-7b2d-9c2f-444444444444"
    processing_claim_id = uuid.uuid4()
    daemon = SimpleNamespace(
        config=SimpleNamespace(name="health"),
        spawner=SimpleNamespace(trigger=AsyncMock()),
    )
    malformed_envelope = {
        "schema_version": "route.v1",
        "request_context": {"request_id": request_id},
        "input": {
            "prompt": "Store a fact.",
            "context": {"conceptual_message": {"excerpts": []}},
        },
    }

    async def _recover_once(*_args, dispatch_fn, **_kwargs):
        await dispatch_fn(
            row_id=row_id,
            route_envelope=malformed_envelope,
            processing_claim_id=processing_claim_id,
            recovery_from_processing=False,
        )
        return 1

    with (
        patch("butlers.switchboard_wiring.route_inbox_recovery_sweep", _recover_once),
        patch(
            "butlers.switchboard_wiring.route_inbox_mark_errored",
            new_callable=AsyncMock,
            return_value=True,
        ) as mark_errored,
        patch("butlers.switchboard_wiring.logger.warning") as recovery_warning,
    ):
        await recover_route_inbox(daemon, pool)

    observability = repr(recovery_warning.call_args_list)
    assert str(row_id) not in observability
    assert request_id not in observability
    assert "Invalid envelope on recovery" in mark_errored.await_args.args[2]


@pytest.mark.parametrize(
    ("result_error", "expected_class"),
    [
        (
            "RuntimeError: 15551234567@s.whatsapp.net PRIVATE MESSAGE SQL SELECT",
            "RuntimeError",
        ),
        ("unstructured 222222222222222@lid PRIVATE MESSAGE", "runtime_unsuccessful"),
    ],
)
async def test_recovery_unsuccessful_result_uses_bounded_failure_class(
    result_error: str,
    expected_class: str,
) -> None:
    """Recovered conceptual routes never persist runtime error remainders."""
    from types import SimpleNamespace

    from butlers.switchboard_wiring import recover_route_inbox

    pool = AsyncMock()
    row_id = uuid.UUID("55555555-5555-4555-8555-555555555555")
    processing_claim_id = uuid.uuid4()
    trigger_result = SimpleNamespace(success=False, error=result_error, session_id=uuid.uuid4())
    daemon = SimpleNamespace(
        config=SimpleNamespace(name="health"),
        spawner=SimpleNamespace(trigger=AsyncMock(return_value=trigger_result)),
    )
    envelope = {
        "schema_version": "route.v1",
        "request_context": _route_request_context(source_channel="whatsapp_user_client"),
        "input": {
            "prompt": "Store facts from this excerpt.",
            "context": {"conceptual_message": {"excerpts": []}},
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

    recovery_span = MagicMock()
    span_context = MagicMock()
    span_context.__enter__.return_value = recovery_span
    span_context.__exit__.return_value = False
    tracer = MagicMock()
    tracer.start_as_current_span.return_value = span_context
    with (
        patch("butlers.switchboard_wiring.route_inbox_recovery_sweep", _recover_once),
        patch(
            "butlers.core.route_inbox.route_inbox_renew_processing_claim",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "butlers.switchboard_wiring.route_inbox_mark_errored",
            new_callable=AsyncMock,
            return_value=True,
        ) as mark_errored,
        patch("butlers.switchboard_wiring.trace.get_tracer", return_value=tracer),
        patch("butlers.switchboard_wiring.logger.warning") as recovery_warning,
    ):
        await recover_route_inbox(daemon, pool)

    persisted_error = mark_errored.await_args.args[2]
    assert persisted_error == f"route runtime returned unsuccessful result ({expected_class})"
    observability = (
        persisted_error
        + repr(recovery_span.set_attribute.call_args_list)
        + repr(recovery_span.set_status.call_args_list)
        + repr(recovery_warning.call_args_list)
    )
    assert "15551234567@s.whatsapp.net" not in observability
    assert "222222222222222@lid" not in observability
    assert "PRIVATE MESSAGE" not in observability
    assert envelope["request_context"]["request_id"] not in observability
    assert str(row_id) not in observability


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


@_booted
@pytest.mark.parametrize(
    "source_channel,envelope_complexity",
    [("telegram_bot", "reasoning"), ("dashboard", "reasoning"), ("telegram_bot", None)],
)
async def test_recovered_route_preserves_live_trigger_inputs(
    booted_daemon,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_channel: str,
    envelope_complexity: str | None,
) -> None:
    """Frozen-path parity: accepted recovery retains the original live route inputs."""
    booted = await _boot(booted_daemon, tmp_path, monkeypatch)
    prompt = "Inspect the attached synthetic diagram."
    if source_channel == "dashboard":
        conversation_id, message_id = await _open_dashboard_turn(booted, prompt)
        thread_identity = str(conversation_id)
    else:
        message_id = uuid.uuid4()
        thread_identity = "synthetic-thread"
    sender_id = str(uuid.uuid4())
    request_context = _live_context(
        source_channel=source_channel,
        source_sender_entity_id=sender_id,
        external_conversation_id="synthetic-external-thread",
        source_thread_identity=thread_identity,
    )
    attachments = [{"media_type": "image/png", "uri": "attachment:synthetic-image"}]
    input_payload: dict[str, Any] = {"prompt": prompt, "attachments": attachments}
    if envelope_complexity is not None:
        input_payload["complexity"] = envelope_complexity

    accepted = await _route(
        booted,
        request_context,
        input=input_payload,
        source_metadata={
            "channel": source_channel,
            "identity": "synthetic-source",
            "tool_name": "ingest",
            "dashboard_message_id": str(message_id),
        },
    )
    await _drain(booted)
    assert accepted["status"] == "accepted"
    (row,) = await _inbox_rows(booted)
    await _make_unprocessed(booted, row["id"])
    await _recover(booted)

    (anchor,) = await booted.daemon.db.pool.fetch("SELECT id FROM public.dashboard_conversations")
    live, recovered = booted.triggers
    expected = {
        "complexity": coerce_complexity_tier(envelope_complexity, strict=False),
        "conversation_id": anchor["id"],
        "attachments": attachments,
        "dashboard_turn_id": message_id if source_channel == "dashboard" else None,
        "request_id": request_context["request_id"],
        "ingestion_event_id": request_context["request_id"],
        "trigger_source": "route",
    }
    for key, value in expected.items():
        assert live[0][key] == value, key
        assert recovered[0][key] == value, key
    assert live[0]["prompt"] == recovered[0]["prompt"]
    assert live[0]["context"] == recovered[0]["context"]
    assert all(not call["route_lease_lost"].is_set() for call, _ in (live, recovered))
    assert all(context["source_entity_id"] == sender_id for _, context in (live, recovered))
    assert all(
        context["request_context"]["source_channel"] == source_channel
        for _, context in (live, recovered)
    )
