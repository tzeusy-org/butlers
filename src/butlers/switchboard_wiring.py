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

from butlers.core.route_inbox import (
    route_inbox_mark_errored,
    route_inbox_mark_processed,
    route_inbox_mark_processing,
    route_inbox_recovery_sweep,
)
from butlers.mcp_patches import apply_streamable_http_client_disconnect_patch
from butlers.core.telemetry import tag_butler_span
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

    pipeline = MessagePipeline(
        switchboard_pool=pool,
        dispatch_fn=daemon.spawner.trigger,
        source_butler="switchboard",
        enable_ingress_dedupe=enable_ingress_dedupe,
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
        endpoint_identity = ref.source.get("endpoint_identity", "unknown")
        external_thread_id = ref.event.get("external_thread_id")
        addressed = bool(ref.source.get("addressed", False))
        request_context: dict[str, Any] = {
            "request_id": ref.request_id,
            "received_at": ref.event.get("observed_at", ""),
            "source_channel": channel,
            "source_endpoint_identity": f"{channel}:{endpoint_identity}",
            "source_sender_identity": ref.sender.get("identity", "unknown"),
            "source_thread_identity": external_thread_id,
            "trace_context": {},
        }
        if addressed:
            request_context["addressed"] = True
        if ref.triage_decision is not None:
            request_context["triage_decision"] = ref.triage_decision
        if ref.triage_target is not None:
            request_context["triage_target"] = ref.triage_target
        if ref.payload_type is not None:
            request_context["payload_type"] = ref.payload_type

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
                        ref.request_id,
                    )

        routing_failed = False
        _routing_error_detail: str | None = None
        _buf_tool_args: dict[str, Any] = {
            "source": channel,
            "source_channel": channel,
            "source_identity": endpoint_identity,
            "source_endpoint_identity": f"{channel}:{endpoint_identity}",
            "sender_identity": ref.sender.get("identity", "unknown"),
            "external_event_id": ref.event.get("external_event_id", ""),
            "external_thread_id": external_thread_id,
            "source_tool": "ingest",
            "request_id": ref.request_id,
            "request_context": request_context,
        }
        if ref.attachments:
            _buf_tool_args["attachments"] = ref.attachments

        try:
            result = await pipeline.process(
                message_text=ref.message_text,
                tool_name="bot_switchboard_handle_message",
                tool_args=_buf_tool_args,
                message_inbox_id=ref.message_inbox_id,
            )
            if result.classification_error or result.routing_error or result.failed_targets:
                routing_failed = True
                _parts = [p for p in [result.classification_error, result.routing_error] if p]
                if result.failed_targets:
                    _parts.append(f"failed_targets: {result.failed_targets}")
                _routing_error_detail = "; ".join(_parts) if _parts else "routing failed"
        except Exception as _buf_exc:
            routing_failed = True
            _routing_error_detail = f"{type(_buf_exc).__name__}: {_buf_exc}"
            logger.exception(
                "DurableBuffer: pipeline processing failed for request_id=%s",
                ref.request_id,
            )

        # Mark the ingestion event as failed/replay_failed, or complete a
        # pending replay back to ingested.
        if routing_failed:
            try:
                from butlers.core.ingestion_events import ingestion_event_mark_failed

                await ingestion_event_mark_failed(pool, ref.request_id, _routing_error_detail)
            except Exception:
                logger.warning(
                    "DurableBuffer: failed to mark ingestion event failed for request_id=%s",
                    ref.request_id,
                )
        else:
            try:
                from butlers.core.ingestion_events import (
                    ingestion_event_mark_replay_complete,
                )

                await ingestion_event_mark_replay_complete(pool, ref.request_id)
            except Exception:
                logger.warning(
                    "DurableBuffer: failed to mark replay complete for request_id=%s",
                    ref.request_id,
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
                        ref.request_id,
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
    """Re-dispatch route_inbox rows that were accepted but never processed.

    Called on startup to recover from crashes or restarts.  Rows in
    'accepted' state older than the grace period are re-dispatched
    as background tasks through the same path as the hot path.

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
    ) -> None:
        """Dispatch one recovered route_inbox row as a background task.

        Recovery tasks always start a fresh root span — there is no live accept-phase
        span to link to (the original request may have come from a previous daemon
        run).  The request_id attribute allows cross-trace correlation via logs.
        """

        try:
            parsed = parse_route_envelope(route_envelope)
        except Exception as exc:
            logger.warning(
                "route_inbox recovery: invalid envelope for id=%s, skipping: %s",
                row_id,
                exc,
            )
            await route_inbox_mark_errored(
                pool,
                row_id,
                f"Invalid envelope on recovery: {exc}",
            )
            return

        route_context = parsed.request_context.model_dump(mode="json")
        route_request_id = str(parsed.request_context.request_id)
        context_text = _build_route_runtime_context(
            route_context=route_context,
            source_channel=parsed.request_context.source_channel,
            conversation_history=parsed.input.conversation_history,
            input_context=parsed.input.context,
            attachments=parsed.input.attachments,
            addressed=parsed.request_context.addressed,
        )
        recovery_prompt = _wrap_routed_message(parsed.input.prompt)

        _tracer = trace.get_tracer("butlers")
        # Fresh root span for recovery — no accept-phase span to link to.
        with _tracer.start_as_current_span(
            "route.process.recovery",
            context=OtelContext(),
        ) as _recovery_span:
            tag_butler_span(_recovery_span, daemon.config.name)
            _recovery_span.set_attribute("request_id", route_request_id)
            await route_inbox_mark_processing(pool, row_id)
            try:
                result = await spawner.trigger(
                    prompt=recovery_prompt,
                    context=context_text,
                    trigger_source="route",
                    request_id=route_request_id,
                )
                await route_inbox_mark_processed(pool, row_id, result.session_id)
            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception("route_inbox recovery: trigger failed for id=%s", row_id)
                _recovery_span.set_status(trace.StatusCode.ERROR, error_msg)
                await route_inbox_mark_errored(pool, row_id, error_msg)

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
