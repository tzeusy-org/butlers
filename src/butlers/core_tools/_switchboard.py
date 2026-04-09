"""Switchboard-specific core tools: ingest, route_to_butler, connector.heartbeat, backfill.*."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from butlers.core.telemetry import tool_span
from butlers.core_tools._base import ToolContext

logger = logging.getLogger(__name__)


def register_switchboard_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register switchboard-only tools: ingest, route_to_butler, connector.heartbeat, backfill.*."""
    if not ctx.is_switchboard:
        return

    import importlib.util as _ilu

    from butlers.core.model_routing import Complexity
    from butlers.modules.pipeline import MessagePipeline, _routing_ctx_var
    from butlers.tools.switchboard.backfill.connector import backfill_poll as _backfill_poll
    from butlers.tools.switchboard.backfill.connector import backfill_progress as _backfill_progress
    from butlers.tools.switchboard.ingestion.ingest import ingest_v1
    from butlers.tools.switchboard.routing.route import route as _switchboard_route

    daemon = ctx.daemon
    pool = ctx.pool
    butler_name = ctx.butler_name

    _hb_path = (
        Path(__file__).resolve().parents[3]
        / "roster"
        / "switchboard"
        / "tools"
        / "connector"
        / "heartbeat.py"
    )
    _hb_spec = _ilu.spec_from_file_location("roster_switchboard_heartbeat", _hb_path)
    assert _hb_spec is not None and _hb_spec.loader is not None
    _hb_mod = _ilu.module_from_spec(_hb_spec)
    _hb_spec.loader.exec_module(_hb_mod)
    _connector_heartbeat = _hb_mod.heartbeat

    pipeline = daemon._pipeline
    # DurableBuffer instance created by _wire_pipelines (may be None if
    # pipeline wiring was skipped, e.g. in tests).
    buffer = daemon._buffer

    # Global-scope ingestion policy evaluator for ingest_v1.
    from butlers.ingestion_policy import IngestionPolicyEvaluator as _IPE

    _global_policy_evaluator = _IPE(scope="global", db_pool=pool)
    asyncio.ensure_future(_global_policy_evaluator.ensure_loaded())

    async def _process_ingested_message(
        pipeline: MessagePipeline,
        request_id: str,
        message_text: str,
        source: dict[str, Any],
        event: dict[str, Any],
        sender: dict[str, Any],
        message_inbox_id: Any,
        triage_decision: str | None = None,
        triage_target: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        """Background task: classify and route an ingested message."""
        from butlers.modules.telegram import (
            REACTION_FAILURE,
            REACTION_IN_PROGRESS,
            REACTION_SUCCESS,
        )

        channel = source.get("channel", "unknown")
        endpoint_identity = source.get("endpoint_identity", "unknown")
        external_thread_id = event.get("external_thread_id")
        addressed = bool(source.get("addressed", False))
        request_context: dict[str, Any] = {
            "request_id": request_id,
            "received_at": event.get("observed_at", ""),
            "source_channel": channel,
            "source_endpoint_identity": f"{channel}:{endpoint_identity}",
            "source_sender_identity": sender.get("identity", "unknown"),
            "source_thread_identity": external_thread_id,
            "trace_context": {},
        }
        if addressed:
            request_context["addressed"] = True
        if triage_decision is not None:
            request_context["triage_decision"] = triage_decision
        if triage_target is not None:
            request_context["triage_target"] = triage_target

        # Resolve TelegramModule for reaction lifecycle (telegram_bot-only).
        _telegram_mod = (
            next((m for m in daemon._active_modules if m.name == "telegram"), None)
            if channel == "telegram_bot"
            else None
        )

        # Fire 👀 reaction before pipeline processing (telegram only).
        if _telegram_mod is not None:
            _react_fn = getattr(_telegram_mod, "react_for_ingest", None)
            if callable(_react_fn):
                try:
                    await _react_fn(
                        external_thread_id=external_thread_id,
                        reaction=REACTION_IN_PROGRESS,
                    )
                except Exception:
                    logger.warning(
                        "Ingest: failed to set in-progress reaction for request_id=%s",
                        request_id,
                    )

        routing_failed = False
        _routing_error_detail: str | None = None

        # Resolve the actual sender identity for identity resolution.
        # For single messages, sender.identity is the sender's ID.
        # For batch messages, sender.identity is "multiple" and per-sender
        # details are in sender.participants / sender.owner_sender_id.
        _sender_identity = sender.get("identity", "unknown")
        _source_id: str | None = None
        _sender_name: str | None = None

        if _sender_identity == "multiple":
            # Batch envelope: extract the primary non-owner sender so
            # identity resolution maps to the correct contact/entity.
            _participants: dict[str, str] = sender.get("participants") or {}
            _owner_sid = sender.get("owner_sender_id")
            _non_owner = [sid for sid in _participants if sid != _owner_sid]
            if _non_owner:
                _source_id = str(_non_owner[0])
                _sender_name = _participants.get(_non_owner[0])
            elif _participants:
                # All participants are the owner (self-chat)
                _first = next(iter(_participants))
                _source_id = str(_first)
                _sender_name = _participants.get(_first)
        elif _sender_identity not in ("unknown", ""):
            # Single message: sender.identity is the actual sender ID.
            _source_id = _sender_identity

        _tool_args: dict[str, Any] = {
            "source": channel,
            "source_channel": channel,
            "source_identity": endpoint_identity,
            "source_endpoint_identity": f"{channel}:{endpoint_identity}",
            "sender_identity": _sender_identity,
            "external_event_id": event.get("external_event_id", ""),
            "external_thread_id": external_thread_id,
            "source_tool": "ingest",
            "request_id": request_id,
            "request_context": request_context,
        }
        if _source_id is not None:
            _tool_args["source_id"] = _source_id
        if _sender_name is not None:
            _tool_args["sender_name"] = _sender_name
        if attachments:
            _tool_args["attachments"] = attachments

        try:
            result = await pipeline.process(
                message_text=message_text,
                tool_name="bot_switchboard_handle_message",
                tool_args=_tool_args,
                message_inbox_id=message_inbox_id,
            )
            if result.classification_error or result.routing_error or result.failed_targets:
                routing_failed = True
                _parts = [
                    p for p in [result.classification_error, result.routing_error] if p
                ]
                if result.failed_targets:
                    _parts.append(f"failed_targets: {result.failed_targets}")
                _routing_error_detail = "; ".join(_parts) if _parts else "routing failed"
        except Exception as _proc_exc:
            routing_failed = True
            _routing_error_detail = f"{type(_proc_exc).__name__}: {_proc_exc}"
            logger.exception(
                "Background pipeline processing failed for request_id=%s",
                request_id,
            )

        # Mark the ingestion event as failed/replay_failed, or complete a
        # pending replay back to ingested.
        if routing_failed:
            try:
                from butlers.core.ingestion_events import ingestion_event_mark_failed

                await ingestion_event_mark_failed(pool, request_id, _routing_error_detail)
            except Exception:
                logger.warning(
                    "Ingest: failed to mark ingestion event failed for request_id=%s",
                    request_id,
                )
        else:
            try:
                from butlers.core.ingestion_events import (
                    ingestion_event_mark_replay_complete,
                )

                await ingestion_event_mark_replay_complete(pool, request_id)
            except Exception:
                logger.warning(
                    "Ingest: failed to mark replay complete for request_id=%s",
                    request_id,
                )

        # Fire ✅ or 👾 reaction after pipeline processing (telegram only).
        if _telegram_mod is not None:
            _react_fn = getattr(_telegram_mod, "react_for_ingest", None)
            if callable(_react_fn):
                terminal_reaction = REACTION_FAILURE if routing_failed else REACTION_SUCCESS
                try:
                    await _react_fn(
                        external_thread_id=external_thread_id,
                        reaction=terminal_reaction,
                    )
                except Exception:
                    logger.warning(
                        "Ingest: failed to set terminal reaction for request_id=%s",
                        request_id,
                    )

    @_core_tool("switchboard_routing")
    @tool_span("ingest", butler_name=butler_name)
    async def ingest(
        schema_version: str,
        source: dict[str, Any],
        event: dict[str, Any],
        sender: dict[str, Any],
        payload: dict[str, Any],
        control: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Accept an ingest.v1 envelope from a connector."""
        envelope: dict[str, Any] = {
            "schema_version": schema_version,
            "source": source,
            "event": event,
            "sender": sender,
            "payload": payload,
        }
        if control is not None:
            envelope["control"] = control
        try:
            result = await ingest_v1(
                pool, envelope, policy_evaluator=_global_policy_evaluator
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

        # Propagate control.addressed into source dict so it flows
        # through the buffer/pipeline into the route request_context.
        if control is not None and control.get("addressed"):
            source["addressed"] = True

        # Extract payload_type from control for decomposition branch
        _payload_type = control.get("payload_type") if control is not None else None

        # Route accepted message via durable buffer (bounded queue)
        # or fall back to direct create_task if buffer is unavailable.
        if not result.duplicate and pipeline is not None:
            normalized_text = payload.get("normalized_text", "")
            # Extract attachment metadata (eager + lazy) for routing context.
            _raw_attachments = payload.get("attachments")
            _attachments: list[dict[str, Any]] | None = (
                list(_raw_attachments)
                if isinstance(_raw_attachments, (list, tuple)) and _raw_attachments
                else None
            )
            if normalized_text:
                if buffer is not None:
                    buffer.enqueue(
                        request_id=str(result.request_id),
                        message_inbox_id=result.request_id,
                        message_text=normalized_text,
                        source=source,
                        event=event,
                        sender=sender,
                        triage_decision=result.triage_decision,
                        triage_target=result.triage_target,
                        attachments=_attachments,
                        payload_type=_payload_type,
                    )
                else:
                    # Fallback: unbounded create_task (buffer not wired)
                    asyncio.create_task(
                        _process_ingested_message(
                            pipeline=pipeline,
                            request_id=str(result.request_id),
                            message_text=normalized_text,
                            source=source,
                            event=event,
                            sender=sender,
                            message_inbox_id=result.request_id,
                            triage_decision=result.triage_decision,
                            triage_target=result.triage_target,
                            attachments=_attachments,
                        ),
                        name=f"ingest-route-{result.request_id}",
                    )

        return result.model_dump(mode="json")

    @_core_tool("switchboard_routing")
    @tool_span("route_to_butler", butler_name=butler_name)
    async def route_to_butler(
        butler: str,
        prompt: str,
        context: str | None = None,
        complexity: str | None = None,
    ) -> dict[str, Any]:
        """ROUTING TOOL — call this to send a message to a specialist butler.

        This is the primary routing tool for the Switchboard. You MUST call
        this tool (not a shell command) to route messages. It may also appear
        in your tool list as ``mcp__switchboard__route_to_butler``.

        Args:
            butler: Target butler name — one of: "finance", "health",
                "relationship", "travel", "education", "lifestyle", "general".
            prompt: Self-contained prompt for the target butler. Must be
                independently understandable without conversation history.
            context: Optional — key details and context the target butler
                needs to act on this request.
            complexity: Task complexity tier — one of "trivial", "medium",
                "high", "extra_high". Defaults to "medium" when omitted.
        """
        from datetime import UTC, datetime

        from butlers.core.tool_call_capture import get_current_runtime_session_routing_context

        _routing_ctx = _routing_ctx_var.get() or {}
        if not isinstance(_routing_ctx, dict):
            _routing_ctx = {}
        runtime_routing_ctx = get_current_runtime_session_routing_context()
        if isinstance(runtime_routing_ctx, dict):
            if not _routing_ctx:
                _routing_ctx = dict(runtime_routing_ctx)
            else:
                for key in (
                    "source_metadata",
                    "request_context",
                    "request_id",
                ):
                    if _routing_ctx.get(key) in (None, "", {}):
                        _routing_ctx[key] = runtime_routing_ctx.get(key)
        source_metadata = _routing_ctx.get("source_metadata", {})
        if not isinstance(source_metadata, dict):
            source_metadata = {}
        normalized_source_metadata: dict[str, Any] = {
            "channel": str(source_metadata.get("channel", "mcp")),
            "identity": str(source_metadata.get("identity", "unknown")),
            "tool_name": str(source_metadata.get("tool_name", "route_to_butler")),
        }
        if source_metadata.get("source_id") not in (None, ""):
            normalized_source_metadata["source_id"] = str(source_metadata["source_id"])
        request_context = _routing_ctx.get("request_context")
        if not isinstance(request_context, dict):
            request_context = None
        raw_request_id = _routing_ctx.get("request_id")
        if raw_request_id in (None, "") and isinstance(request_context, dict):
            raw_request_id = request_context.get("request_id")
        request_id = MessagePipeline._coerce_request_id(raw_request_id)
        source_channel = str(
            request_context.get("source_channel")
            if isinstance(request_context, dict)
            and request_context.get("source_channel") not in (None, "")
            else normalized_source_metadata["channel"]
        )
        source_sender_identity = str(
            request_context.get("source_sender_identity")
            if isinstance(request_context, dict)
            and request_context.get("source_sender_identity") not in (None, "")
            else normalized_source_metadata["identity"]
        )
        source_thread_identity = (
            request_context.get("source_thread_identity")
            if isinstance(request_context, dict)
            else None
        )

        # Prepend identity preamble to prompt if present in routing context.
        identity_preamble = _routing_ctx.get("identity_preamble")
        effective_prompt = f"{identity_preamble}\n{prompt}" if identity_preamble else prompt

        # Normalize complexity: accept valid enum values, default to medium.
        _complexity_values = {c.value for c in Complexity}
        _raw_complexity = complexity.strip().lower() if isinstance(complexity, str) else ""
        _normalized_complexity = (
            _raw_complexity
            if _raw_complexity in _complexity_values
            else Complexity.MEDIUM.value
        )

        # Forward attachment metadata from routing context so target
        # butlers know what attachments exist and can fetch on demand.
        _route_attachments = _routing_ctx.get("attachments")

        _input: dict[str, Any] = {
            "prompt": effective_prompt,
            "context": context,
            "complexity": _normalized_complexity,
        }
        if _route_attachments:
            _input["attachments"] = _route_attachments

        # Structured sender identity from resolution (contact_id, entity_id).
        rc: dict[str, Any] = {
            "request_id": request_id,
            "received_at": datetime.now(UTC).isoformat(),
            "source_channel": source_channel,
            "source_endpoint_identity": "switchboard",
            "source_sender_identity": source_sender_identity,
            "source_thread_identity": source_thread_identity,
            "trace_context": {},
        }
        _src_contact_id = _routing_ctx.get("source_contact_id")
        _src_entity_id = _routing_ctx.get("source_entity_id")
        if _src_contact_id:
            rc["source_sender_contact_id"] = _src_contact_id
        if _src_entity_id:
            rc["source_sender_entity_id"] = _src_entity_id

        envelope: dict[str, Any] = {
            "schema_version": "route.v1",
            "request_context": rc,
            "input": _input,
            "target": {"butler": butler, "tool": "route.execute"},
            "source_metadata": normalized_source_metadata,
            "__switchboard_route_context": {
                "request_id": request_id,
                "fanout_mode": "tool_routed",
                "segment_id": f"route-{butler}",
                "attempt": 1,
            },
        }

        try:
            result = await _switchboard_route(
                pool,
                target_butler=butler,
                tool_name="route.execute",
                args=envelope,
                source_butler="switchboard",
            )
            if isinstance(result, dict) and result.get("error"):
                return {
                    "status": "error",
                    "butler": butler,
                    "error": str(result["error"]),
                }
            # Pass through 'accepted' or 'error' status from the target butler so
            # that telemetry and the runtime can see actual outcomes.
            inner = result.get("result") if isinstance(result, dict) else None
            if isinstance(inner, dict):
                if inner.get("status") == "accepted":
                    return {"status": "accepted", "butler": butler}
                if inner.get("status") == "error":
                    error_detail = inner.get("error", {})
                    error_msg = (
                        error_detail.get("message", str(error_detail))
                        if isinstance(error_detail, dict)
                        else str(error_detail)
                    )
                    logger.warning(
                        "route_to_butler: target %s returned error: %s",
                        butler,
                        error_msg,
                    )
                    return {
                        "status": "error",
                        "butler": butler,
                        "error": error_msg,
                    }
            # Unexpected response shape — log and surface as error so
            # the failure is visible instead of silently swallowed.
            logger.warning(
                "route_to_butler: target %s returned unexpected response "
                "(type=%s, inner_type=%s): %s",
                butler,
                type(result).__name__,
                type(inner).__name__ if inner is not None else "None",
                str(result)[:500],
            )
            return {
                "status": "error",
                "butler": butler,
                "error": (
                    f"Unexpected response from {butler}: "
                    f"expected dict with status 'accepted', "
                    f"got {type(inner).__name__}"
                ),
            }
        except Exception as exc:
            logger.warning(
                "route_to_butler failed for %s: %s",
                butler,
                exc,
            )
            return {
                "status": "error",
                "butler": butler,
                "error": f"{type(exc).__name__}: {exc}",
            }

    @_core_tool("switchboard_routing", name="connector.heartbeat")
    @tool_span("connector.heartbeat", butler_name=butler_name)
    async def connector_heartbeat(
        schema_version: str,
        connector: dict[str, Any],
        status: dict[str, Any],
        counters: dict[str, Any],
        checkpoint: dict[str, Any] | None = None,
        capabilities: dict[str, Any] | None = None,
        sent_at: str = "",
    ) -> dict[str, Any]:
        """Accept a connector heartbeat for liveness tracking and statistics."""
        payload = {
            "schema_version": schema_version,
            "connector": connector,
            "status": status,
            "counters": counters,
            "sent_at": sent_at,
        }
        if checkpoint is not None:
            payload["checkpoint"] = checkpoint
        if capabilities is not None:
            payload["capabilities"] = capabilities
        result = await _connector_heartbeat(pool, payload)
        return result.model_dump()

    @_core_tool("switchboard_backfill", name="backfill.poll")
    @tool_span("backfill.poll", butler_name=butler_name)
    async def backfill_poll_tool(
        connector_type: str,
        endpoint_identity: str,
    ) -> dict[str, Any] | None:
        """Claim the next pending backfill job for a connector identity.

        Called by connector processes (e.g. Gmail connector) to atomically
        claim the oldest pending backfill job. Returns None when no pending
        job exists for this connector.

        Connectors MUST call this no more frequently than once every 60 seconds.

        Args:
            connector_type: Canonical connector type (e.g. ``gmail``).
            endpoint_identity: The account identity this connector serves.

        Returns:
            Job payload with job_id, params, and cursor on success; None when
            no pending job is available.
        """
        return await _backfill_poll(
            pool,
            connector_type=connector_type,
            endpoint_identity=endpoint_identity,
        )

    @_core_tool("switchboard_backfill", name="backfill.progress")
    @tool_span("backfill.progress", butler_name=butler_name)
    async def backfill_progress_tool(
        job_id: str,
        connector_type: str,
        endpoint_identity: str,
        rows_processed: int,
        rows_skipped: int,
        cost_spent_cents_delta: int,
        cursor: dict[str, Any] | None = None,
        status: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Report batch progress for an active backfill job.

        Called by connector processes to update cumulative counters, advance
        the resume cursor, and optionally mark the job as completed or errored.

        Connectors MUST stop processing when the returned status is anything
        other than ``active``.

        Args:
            job_id: UUID of the job being reported on.
            connector_type: Must match the job's connector_type.
            endpoint_identity: Must match the job's endpoint_identity.
            rows_processed: Rows processed in this batch (non-negative).
            rows_skipped: Rows skipped in this batch (non-negative).
            cost_spent_cents_delta: Additional cost in cents for this batch.
            cursor: Optional updated resume cursor (opaque JSONB).
            status: Optional terminal status (``completed`` or ``error``).
            error: Optional error detail (accompany ``status="error"``).

        Returns:
            ``{status: str}`` — the authoritative job status after this update.
        """
        return await _backfill_progress(
            pool,
            job_id=job_id,
            connector_type=connector_type,
            endpoint_identity=endpoint_identity,
            rows_processed=rows_processed,
            rows_skipped=rows_skipped,
            cost_spent_cents_delta=cost_spent_cents_delta,
            cursor=cursor,
            status=status,
            error=error,
        )
