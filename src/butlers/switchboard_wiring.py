"""Switchboard-specific wiring helpers extracted from daemon.py.

All pipeline/routing logic that is only relevant when the butler is the
switchboard lives here.  ButlerDaemon delegates to these functions
conditionally; non-switchboard butlers never call them.

The functions accept a :class:`~butlers.daemon.ButlerDaemon` instance typed
as ``Any`` at runtime to avoid a circular import between ``daemon.py`` and
this module.  The same pattern is used in :mod:`butlers.lifecycle`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

import anyio
import asyncpg
from fastmcp import Client as MCPClient
from opentelemetry import trace
from opentelemetry.context import Context as OtelContext

from butlers.core.dashboard_turns import reconcile_route_recovery
from butlers.core.route_inbox import (
    RouteInboxLeaseLost,
    route_inbox_mark_errored,
    route_inbox_mark_processed,
    route_inbox_processing_lease_heartbeat,
    route_inbox_recovery_sweep,
    route_inbox_wait_while_claimed,
)
from butlers.core.route_observability import (
    has_conceptual_route_context,
    opaque_route_ref,
    safe_runtime_failure_class,
)
from butlers.core.routing_context import _routing_ctx_var
from butlers.core.spawner import SESSION_CANCELLED_ERROR
from butlers.core.telemetry import tag_butler_span
from butlers.mcp_patches import apply_streamable_http_client_disconnect_patch
from butlers.routing_guidance import (
    _build_route_runtime_context,
    _wrap_routed_message,
)
from butlers.tools.switchboard.routing.contracts import parse_route_envelope

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_SWITCHBOARD_HEARTBEAT_INTERVAL_S = 30
_SWITCHBOARD_HEARTBEAT_TIMEOUT_S = 5.0
_STALE_SWITCHBOARD_CONNECTION_ERRORS = (
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
)


def build_buffer_pipeline_inputs(ref: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build request context and pipeline tool args for a buffered inbox ref."""
    channel = ref.source.get("channel", "unknown")
    endpoint_identity = ref.source.get("endpoint_identity", "unknown")
    external_thread_id = ref.event.get("external_thread_id")
    addressed = bool(ref.source.get("addressed", False))
    sender_identity = ref.sender.get("identity", "unknown")

    source_id: str | None = None
    sender_name: str | None = None
    participants: Any = None
    owner_sender_id: Any = None
    if sender_identity == "multiple":
        participants = ref.sender.get("participants") or {}
        owner_sender_id = ref.sender.get("owner_sender_id")
        if isinstance(participants, dict):
            non_owner = [str(sid) for sid in participants if str(sid) != str(owner_sender_id)]
            if non_owner:
                source_id = non_owner[0]
                sender_name = participants.get(source_id)
            elif participants:
                first = str(next(iter(participants)))
                source_id = first
                sender_name = participants.get(first)
    elif sender_identity not in ("unknown", ""):
        source_id = str(sender_identity)

    source_sender_identity = source_id or sender_identity
    request_context: dict[str, Any] = {
        "request_id": ref.request_id,
        "received_at": ref.event.get("observed_at", ""),
        "source_channel": channel,
        "source_endpoint_identity": f"{channel}:{endpoint_identity}",
        "source_sender_identity": source_sender_identity,
        "source_thread_identity": external_thread_id,
        "trace_context": {},
    }
    if addressed:
        request_context["addressed"] = True
    if isinstance(participants, dict) and participants:
        request_context["source_sender_identities"] = sorted(str(key) for key in participants)
    if owner_sender_id:
        request_context["owner_sender_identity"] = str(owner_sender_id)
    if ref.triage_decision is not None:
        request_context["triage_decision"] = ref.triage_decision
    if ref.triage_target is not None:
        request_context["triage_target"] = ref.triage_target
    if ref.payload_type is not None:
        request_context["payload_type"] = ref.payload_type

    tool_args: dict[str, Any] = {
        "source": channel,
        "source_channel": channel,
        "source_identity": endpoint_identity,
        "source_endpoint_identity": f"{channel}:{endpoint_identity}",
        "sender_identity": sender_identity,
        "external_event_id": ref.event.get("external_event_id", ""),
        "external_thread_id": external_thread_id,
        "source_tool": "ingest",
        "request_id": ref.request_id,
        "request_context": request_context,
    }
    if channel == "dashboard":
        # The dashboard envelope's external event id is the immutable
        # ``dashboard_messages.id``. Preserve it separately from source_id
        # (sender identity) so every downstream Spawner can join the durable
        # Stop protocol for this exact user turn.
        tool_args["dashboard_message_id"] = ref.event.get("external_event_id", "")
    if source_id is not None:
        tool_args["source_id"] = source_id
    if sender_name is not None:
        tool_args["sender_name"] = sender_name
    if ref.attachments:
        tool_args["attachments"] = ref.attachments

    return request_context, tool_args


def wire_pipelines(daemon: Any, pool: Any) -> None:
    """Attach a MessagePipeline to modules that support set_pipeline().

    Only the switchboard butler classifies and routes inbound channel
    messages. Other butlers skip pipeline wiring entirely.

    Also creates the DurableBuffer that replaces the unbounded
    asyncio.create_task() dispatch with a bounded in-memory queue.

    This is the implementation extracted from
    :meth:`~butlers.daemon.ButlerDaemon._wire_pipelines`.
    """
    # Intentional name check: pipeline wiring and DurableBuffer are switchboard-specific
    # behaviors, not a generic staffer concern. Other staffers (e.g. messenger) do not
    # classify or buffer inbound channel messages.
    if daemon.config.name != "switchboard":
        return
    if daemon.spawner is None:
        return

    from butlers.modules.pipeline import MessagePipeline, PipelineModule

    # Read enable_ingress_dedupe from PipelineModule config if the module is active.
    pipeline_mod = next(
        (m for m in daemon._active_modules if isinstance(m, PipelineModule)),
        None,
    )
    enable_ingress_dedupe = (
        pipeline_mod._config.enable_ingress_dedupe if pipeline_mod is not None else True
    )
    classification_timeout_s = (
        pipeline_mod._config.classification_timeout_s if pipeline_mod is not None else None
    )

    async def _notify_owner_about_unknown_sender(message: str) -> None:
        """Deliver one entity-review notice through the normal notify boundary.

        This callback is deliberately deterministic and narrow: identity ingress
        has already constructed a content-free review message, and this seam
        only resolves the configured owner Telegram recipient before sending a
        ``notify.v1`` envelope through Switchboard → Messenger.  It must raise
        for every unsent result so identity injection can log the failed attempt
        while retaining its durable no-repeat claim.
        """
        recipient = await daemon._resolve_default_notify_recipient(
            channel="telegram",
            intent="send",
            recipient=None,
        )
        if not recipient:
            raise RuntimeError(
                "No Telegram recipient configured for unknown-sender owner notification"
            )

        from butlers.tools.switchboard.notification.deliver import deliver

        result = await deliver(
            pool,
            source_butler="switchboard",
            notify_request={
                "schema_version": "notify.v1",
                "origin_butler": "switchboard",
                "delivery": {
                    "intent": "send",
                    "channel": "telegram",
                    "recipient": recipient,
                    "message": message,
                },
            },
        )
        if not isinstance(result, dict) or result.get("status") != "sent":
            detail = result.get("error") if isinstance(result, dict) else None
            raise RuntimeError(f"Unknown-sender owner notification failed: {detail or result}")

    pipeline = MessagePipeline(
        switchboard_pool=pool,
        dispatch_fn=daemon.spawner.trigger,
        source_butler="switchboard",
        enable_ingress_dedupe=enable_ingress_dedupe,
        classification_timeout_s=classification_timeout_s,
        enable_identity_resolution=True,
        notify_owner_fn=_notify_owner_about_unknown_sender,
        # daemon.mcp is not yet assigned a real FastMCP instance at this
        # point in startup (see butlers.lifecycle.run_startup: _wire_pipelines
        # runs at step 10b, daemon.mcp = FastMCP(...) at step 12) — a provider
        # closure defers the read until the first classification dispatch,
        # by which point startup has long finished.
        local_tool_server_provider=lambda: daemon.mcp,
        credential_store=daemon._credential_store,
    )
    daemon._pipeline = pipeline

    # Capture TelegramModule reference for reaction lifecycle in the ingest path.
    # If not active (module absent or disabled), telegram_mod is None and
    # reaction calls are silently skipped.
    telegram_mod = next(
        (m for m in daemon._active_modules if m.name == "telegram"),
        None,
    )

    # Build the process function that wraps pipeline.process()
    async def _buffer_process(ref: Any) -> None:
        from butlers.core.buffer import _MessageRef
        from butlers.modules.telegram import (
            REACTION_FAILURE,
            REACTION_IN_PROGRESS,
            REACTION_SUCCESS,
        )

        if not isinstance(ref, _MessageRef):
            return
        channel = ref.source.get("channel", "unknown")
        content_blind_observability = (
            channel == "whatsapp_user_client" or ref.payload_type == "conversation_history"
        )
        observability_request_id = (
            opaque_route_ref(ref.request_id) if content_blind_observability else ref.request_id
        )
        external_thread_id = ref.event.get("external_thread_id")
        _, _buf_tool_args = build_buffer_pipeline_inputs(ref)

        # Fire reaction before pipeline processing (telegram_bot only).
        if channel == "telegram_bot" and telegram_mod is not None:
            react_fn = getattr(telegram_mod, "react_for_ingest", None)
            if callable(react_fn):
                try:
                    await react_fn(
                        external_thread_id=external_thread_id,
                        reaction=REACTION_IN_PROGRESS,
                    )
                except Exception:
                    logger.warning(
                        "DurableBuffer: failed to set in-progress reaction for request_id=%s",
                        observability_request_id,
                    )

        routing_failed = False
        _routing_error_detail: str | None = None
        try:
            result = await pipeline.process(
                message_text=ref.message_text,
                tool_name="bot_switchboard_handle_message",
                tool_args=_buf_tool_args,
                message_inbox_id=ref.message_inbox_id,
            )
            if result.classification_error or result.routing_error or result.failed_targets:
                routing_failed = True
                _parts: list[str] = []
                if result.classification_error:
                    _parts.append("classification_error")
                if result.routing_error:
                    _parts.append("routing_error")
                if result.failed_targets:
                    _parts.append(f"failed_targets:{len(result.failed_targets)}")
                _routing_error_detail = "; ".join(_parts) if _parts else "routing failed"
        except Exception as _buf_exc:
            routing_failed = True
            failure_class = type(_buf_exc).__name__
            _routing_error_detail = f"pipeline_exception:{failure_class}"
            logger.warning(
                "DurableBuffer: pipeline processing failed for request_id=%s failure_class=%s",
                observability_request_id,
                failure_class,
            )

        # Mark the ingestion event as failed/replay_failed, or complete a
        # pending replay back to ingested. Shielded (see
        # ingestion_event_reconcile_after_processing) so a worker cancelled by
        # DurableBuffer.stop()'s shutdown drain timeout — a real risk for
        # slower, multi-target dispatches like email digests — cannot strand
        # the ingestion_events row after processing already succeeded.
        from butlers.core.ingestion_events import ingestion_event_reconcile_after_processing

        await ingestion_event_reconcile_after_processing(
            pool,
            ref.request_id,
            routing_failed=routing_failed,
            error_detail=_routing_error_detail,
        )

        # Fire terminal reaction after pipeline processing (telegram_bot only).
        if channel == "telegram_bot" and telegram_mod is not None:
            react_fn = getattr(telegram_mod, "react_for_ingest", None)
            if callable(react_fn):
                terminal_reaction = REACTION_FAILURE if routing_failed else REACTION_SUCCESS
                try:
                    await react_fn(
                        external_thread_id=external_thread_id,
                        reaction=terminal_reaction,
                    )
                except Exception:
                    logger.warning(
                        "DurableBuffer: failed to set terminal reaction for request_id=%s",
                        observability_request_id,
                    )

    # Create the durable buffer
    from butlers.core.buffer import DurableBuffer

    daemon._buffer = DurableBuffer(
        config=daemon.config.buffer,
        pool=pool,
        process_fn=_buffer_process,
    )

    wired_modules: list[str] = []
    for mod in daemon._active_modules:
        set_pipeline = getattr(mod, "set_pipeline", None)
        if callable(set_pipeline):
            set_pipeline(pipeline)
            wired_modules.append(mod.name)

    if wired_modules:
        logger.info(
            "Wired message pipeline for module(s): %s",
            ", ".join(sorted(wired_modules)),
        )


async def recover_route_inbox(daemon: Any, pool: asyncpg.Pool) -> None:
    """Recover eligible route-inbox rows under a fenced processing lease.

    Called on startup to recover from crashes or restarts.  Rows in ``accepted``
    state and stale ``processing`` leases are eligible for recovery through the normal
    dispatch path, except that a reclaimed dashboard processing row first
    reconciles its durable predecessor and never replays it when unprovable.

    This is the implementation extracted from
    :meth:`~butlers.daemon.ButlerDaemon._recover_route_inbox`.
    """
    if daemon.spawner is None:
        return

    spawner = daemon.spawner  # capture for closures

    async def _dispatch_recovered(
        *,
        row_id: uuid.UUID,
        route_envelope: dict,
        processing_claim_id: uuid.UUID,
        recovery_from_processing: bool,
    ) -> None:
        """Dispatch one recovered route_inbox row as a background task.

        Recovery tasks always start a fresh root span — there is no live accept-phase
        span to link to (the original request may have come from a previous daemon
        run).  The request_id attribute allows cross-trace correlation via logs.
        """

        raw_content_blind_recovery = has_conceptual_route_context(route_envelope)
        preparse_observability_row_id = (
            opaque_route_ref(row_id) if raw_content_blind_recovery else row_id
        )
        try:
            parsed = parse_route_envelope(route_envelope)
        except Exception as exc:
            failure_class = type(exc).__name__
            logger.warning(
                "route_inbox recovery: invalid envelope for id=%s, skipping failure_class=%s",
                preparse_observability_row_id,
                failure_class,
            )
            await route_inbox_mark_errored(
                pool,
                row_id,
                f"Invalid envelope on recovery ({failure_class})",
                processing_claim_id=processing_claim_id,
            )
            return

        route_context = parsed.request_context.model_dump(mode="json")
        route_request_id = str(parsed.request_context.request_id)
        dashboard_turn_id = (
            parsed.source_metadata.dashboard_message_id
            if (
                parsed.request_context.source_channel == "dashboard"
                and parsed.source_metadata is not None
            )
            else None
        )
        context_text = _build_route_runtime_context(
            route_context=route_context,
            source_channel=parsed.request_context.source_channel,
            conversation_history=parsed.input.conversation_history,
            input_context=parsed.input.context,
            attachments=parsed.input.attachments,
            addressed=parsed.request_context.addressed,
        )
        recovery_prompt = _wrap_routed_message(parsed.input.prompt)
        recovered_routing_context: dict[str, Any] = {}
        if parsed.request_context.source_sender_entity_id:
            recovered_routing_context["source_entity_id"] = (
                parsed.request_context.source_sender_entity_id
            )
        if isinstance(parsed.input.context, dict):
            conceptual_message = parsed.input.context.get("conceptual_message")
            if isinstance(conceptual_message, dict):
                recovered_routing_context["conceptual_message"] = dict(conceptual_message)
        content_blind_recovery = "conceptual_message" in recovered_routing_context
        observability_request_id = (
            opaque_route_ref(route_request_id) if content_blind_recovery else route_request_id
        )
        observability_row_id = opaque_route_ref(row_id) if content_blind_recovery else row_id

        _tracer = trace.get_tracer("butlers")
        # Fresh root span for recovery — no accept-phase span to link to.
        with _tracer.start_as_current_span(
            "route.process.recovery",
            context=OtelContext(),
        ) as _recovery_span:
            tag_butler_span(_recovery_span, daemon.config.name)
            _recovery_span.set_attribute("request_id", observability_request_id)
            try:
                async with route_inbox_processing_lease_heartbeat(
                    pool,
                    row_id,
                    processing_claim_id,
                ) as lease_lost:
                    _routing_ctx_var.set(recovered_routing_context or None)
                    if dashboard_turn_id is not None and recovery_from_processing:
                        try:
                            recovery_control = await reconcile_route_recovery(
                                pool,
                                message_id=dashboard_turn_id,
                                request_id=parsed.request_context.request_id,
                                route_inbox_id=row_id,
                            )
                        except Exception:
                            logger.exception(
                                "route_inbox recovery: could not reconcile dashboard "
                                "predecessor id=%s",
                                row_id,
                            )
                            if lease_lost.is_set():
                                raise RouteInboxLeaseLost(
                                    "route inbox processing lease was lost during "
                                    "dashboard recovery reconciliation"
                                )
                            await route_inbox_mark_errored(
                                pool,
                                row_id,
                                "Could not reconcile the prior dashboard runtime before recovery.",
                                processing_claim_id=processing_claim_id,
                            )
                            return
                        if lease_lost.is_set():
                            raise RouteInboxLeaseLost(
                                "route inbox processing lease was lost during "
                                "dashboard recovery reconciliation"
                            )
                        if recovery_control.outcome in {"cancelled", "finished"}:
                            if not await route_inbox_mark_processed(
                                pool,
                                row_id,
                                None,
                                processing_claim_id=processing_claim_id,
                            ):
                                raise RouteInboxLeaseLost(
                                    "route inbox processing lease was lost while marking "
                                    "the recovered dashboard turn"
                                )
                            return
                        # A reclaimed dashboard row represents a predecessor
                        # runtime whose termination cannot be established by
                        # the route inbox alone.  Only a terminal prior turn
                        # can safely settle this row; every other present or
                        # future reconciliation result must fail closed rather
                        # than start a duplicate runtime.
                        await route_inbox_mark_errored(
                            pool,
                            row_id,
                            "Dashboard recovery could not prove the prior runtime stopped; "
                            f"automatic replay was suppressed ({recovery_control.outcome}).",
                            processing_claim_id=processing_claim_id,
                        )
                        return

                    result = await route_inbox_wait_while_claimed(
                        pool,
                        row_id,
                        processing_claim_id,
                        lease_lost,
                        lambda: spawner.trigger(
                            prompt=recovery_prompt,
                            context=context_text,
                            trigger_source="route",
                            request_id=route_request_id,
                            # Recovery dispatches replay the original ingest;
                            # request_id is the same UUID7 the switchboard wrote
                            # as public.ingestion_events.id, so persist it as the
                            # session's ingestion_event_id FK to preserve the
                            # chronicler contact-resolution join (mirrors the
                            # primary route handler in core_tools/_routing.py).
                            ingestion_event_id=route_request_id,
                            # Recovered dashboard work must rejoin the same durable
                            # turn, otherwise a Stop during a process restart could
                            # not prevent the replayed target runtime from starting.
                            dashboard_turn_id=dashboard_turn_id,
                            route_lease_lost=lease_lost,
                        ),
                    )
                    if lease_lost.is_set():
                        raise RouteInboxLeaseLost(
                            "route inbox processing lease was lost after runtime completion"
                        )
                    result_error = getattr(result, "error", None)
                    if not bool(getattr(result, "success", False)) and (
                        result_error != SESSION_CANCELLED_ERROR
                    ):
                        result_failure_class = safe_runtime_failure_class(result_error)
                        error_msg = (
                            f"route runtime returned unsuccessful result ({result_failure_class})"
                        )
                        _recovery_span.set_status(trace.StatusCode.ERROR, error_msg)
                        await route_inbox_mark_errored(
                            pool,
                            row_id,
                            error_msg,
                            processing_claim_id=processing_claim_id,
                        )
                        return
                    if not await route_inbox_mark_processed(
                        pool,
                        row_id,
                        result.session_id,
                        processing_claim_id=processing_claim_id,
                    ):
                        raise RouteInboxLeaseLost(
                            "route inbox processing lease was lost while marking the result"
                        )
            except RouteInboxLeaseLost:
                # Do not race the recovery owner with a terminal write after
                # heartbeat ownership is lost.  A later sweep reconciles it.
                logger.warning(
                    "route_inbox recovery: relinquished processing lease id=%s",
                    observability_row_id,
                )
                return
            except Exception as exc:
                failure_class = type(exc).__name__
                error_msg = f"route recovery trigger failed ({failure_class})"
                logger.warning(
                    "route_inbox recovery: trigger failed for id=%s failure_class=%s",
                    observability_row_id,
                    failure_class,
                )
                _recovery_span.set_status(trace.StatusCode.ERROR, error_msg)
                await route_inbox_mark_errored(
                    pool,
                    row_id,
                    error_msg,
                    processing_claim_id=processing_claim_id,
                )

    try:
        recovered = await route_inbox_recovery_sweep(
            pool,
            dispatch_fn=_dispatch_recovered,
        )
        if recovered:
            logger.info(
                "Butler %s: recovered %d unprocessed route_inbox row(s) on startup",
                daemon.config.name,
                recovered,
            )
    except Exception:
        logger.exception(
            "Butler %s: route_inbox recovery sweep failed on startup",
            daemon.config.name,
        )


async def connect_switchboard(daemon: Any) -> None:
    """Open an MCP client connection to the Switchboard butler.

    Skips connection for the Switchboard butler itself (it IS the
    Switchboard) and when no ``switchboard_url`` is configured.

    Connection failures are logged as warnings but do not prevent
    butler startup — the butler can operate without the Switchboard,
    though the ``notify()`` tool will return errors until the
    connection is established.

    The FastMCP Client is entered as a long-lived async context
    manager (via ``__aenter__``). :func:`disconnect_switchboard` calls
    ``__aexit__`` to clean up.

    This is the implementation extracted from
    :meth:`~butlers.daemon.ButlerDaemon._connect_switchboard`.
    """
    url = daemon.config.switchboard_url
    if url is None:
        logger.debug(
            "No switchboard_url configured for %s; skipping Switchboard connection",
            daemon.config.name,
        )
        return

    try:
        apply_streamable_http_client_disconnect_patch()
        client = MCPClient(url, name=f"butler-{daemon.config.name}")
        await client.__aenter__()
        daemon.switchboard_client = client
        logger.info("Connected to Switchboard at %s for butler %s", url, daemon.config.name)
    except Exception:
        logger.warning(
            "Switchboard not yet reachable at %s for butler %s; "
            "notify() will be unavailable until Switchboard is up",
            url,
            daemon.config.name,
        )


async def disconnect_switchboard(daemon: Any) -> None:
    """Close the Switchboard MCP client connection if open.

    This is the implementation extracted from
    :meth:`~butlers.daemon.ButlerDaemon._disconnect_switchboard`.
    """
    if daemon.switchboard_client is not None:
        try:
            await daemon.switchboard_client.__aexit__(None, None, None)
            logger.info("Disconnected from Switchboard")
        except Exception:
            logger.warning("Error closing Switchboard client", exc_info=True)
        finally:
            daemon.switchboard_client = None


async def switchboard_heartbeat_loop(daemon: Any) -> None:
    """Periodically check and re-establish the Switchboard connection.

    Runs as a background task for the lifetime of the butler.  On each
    tick it either attempts to connect (when ``switchboard_client`` is
    ``None``) or probes liveness of the existing connection via
    ``ping()``. A failed probe triggers a disconnect + reconnect.

    All exceptions (except ``CancelledError``) are swallowed so that the
    heartbeat never crashes the butler.

    This is the implementation extracted from
    :meth:`~butlers.daemon.ButlerDaemon._switchboard_heartbeat_loop`.
    """
    try:
        while True:
            await asyncio.sleep(_SWITCHBOARD_HEARTBEAT_INTERVAL_S)
            try:
                if daemon.switchboard_client is None:
                    logger.debug("Switchboard heartbeat: client is None, attempting reconnect")
                    await connect_switchboard(daemon)
                else:
                    try:
                        await asyncio.wait_for(
                            daemon.switchboard_client.ping(),
                            timeout=_SWITCHBOARD_HEARTBEAT_TIMEOUT_S,
                        )
                    except _STALE_SWITCHBOARD_CONNECTION_ERRORS:
                        logger.info(
                            "Switchboard heartbeat: stale connection detected, reconnecting",
                            exc_info=True,
                        )
                        await disconnect_switchboard(daemon)
                        await connect_switchboard(daemon)
                    except Exception:
                        logger.warning(
                            "Switchboard heartbeat: connection dead, reconnecting",
                            exc_info=True,
                        )
                        await disconnect_switchboard(daemon)
                        await connect_switchboard(daemon)
            except Exception:
                logger.warning("Switchboard heartbeat: unexpected error", exc_info=True)
    except asyncio.CancelledError:
        return
