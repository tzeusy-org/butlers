"""Message classification and routing pipeline for input modules.

Provides a ``MessagePipeline`` that connects input modules (Telegram, Email)
to the switchboard's ``classify_message()`` and ``route()`` functions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RoutingResult:
    """Result of classifying and routing a message through the pipeline."""

    target_butler: str
    route_result: dict[str, Any] = field(default_factory=dict)
    classification_error: str | None = None
    routing_error: str | None = None
    routed_targets: list[str] = field(default_factory=list)
    acked_targets: list[str] = field(default_factory=list)
    failed_targets: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _IngressDedupeRecord:
    request_id: Any
    decision: str
    dedupe_key: str
    dedupe_strategy: str


class MessagePipeline:
    """Connects input modules to the switchboard classification and routing.

    Parameters
    ----------
    switchboard_pool:
        asyncpg Pool connected to the switchboard butler's database
        (where butler_registry and routing_log tables live).
    dispatch_fn:
        Async callable used by ``classify_message`` to spawn a CC instance.
        Typically ``spawner.trigger``.
    source_butler:
        Name of the butler that owns this pipeline (used in routing logs).
    classify_fn:
        Optional override for the classification function.  Defaults to
        ``switchboard.classify_message``.
    route_fn:
        Optional override for the routing function.  Defaults to
        ``switchboard.route``.
    """

    def __init__(
        self,
        switchboard_pool: Any,
        dispatch_fn: Callable[..., Coroutine],
        source_butler: str = "switchboard",
        *,
        classify_fn: Callable[..., Coroutine] | None = None,
        route_fn: Callable[..., Coroutine] | None = None,
        enable_ingress_dedupe: bool = False,
    ) -> None:
        self._pool = switchboard_pool
        self._dispatch_fn = dispatch_fn
        self._source_butler = source_butler
        self._classify_fn = classify_fn
        self._route_fn = route_fn
        self._enable_ingress_dedupe = enable_ingress_dedupe

    @staticmethod
    def _default_identity_for_tool(tool_name: str) -> str:
        if tool_name.startswith("user_"):
            return "user"
        if tool_name.startswith("bot_"):
            return "bot"
        return "unknown"

    @classmethod
    def _build_source_metadata(
        cls,
        args: dict[str, Any],
        *,
        tool_name: str,
    ) -> dict[str, str]:
        channel = str(args.get("source_channel") or args.get("source") or "unknown")
        identity = str(args.get("source_identity") or cls._default_identity_for_tool(tool_name))
        source_tool = str(args.get("source_tool") or tool_name)

        metadata: dict[str, str] = {
            "channel": channel,
            "identity": identity,
            "tool_name": source_tool,
        }
        if args.get("source_id") not in (None, ""):
            metadata["source_id"] = str(args["source_id"])
        return metadata

    @staticmethod
    def _message_preview(text: str, max_chars: int = 80) -> str:
        compact = " ".join(text.split())
        if len(compact) <= max_chars:
            return compact
        return f"{compact[: max_chars - 3]}..."

    @staticmethod
    def _log_fields(
        *,
        source: str,
        chat_id: str | None,
        target_butler: str | None,
        latency_ms: float | None,
        **extra: Any,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "source": source,
            "chat_id": chat_id,
            "target_butler": target_butler,
            "latency_ms": latency_ms,
        }
        fields.update(extra)
        return fields

    @staticmethod
    def _normalize_classification(
        classification: Any,
        *,
        fallback_message: str,
    ) -> tuple[str, str]:
        """Normalize classifier output into ``(target_butler, routed_message)``.

        Supports both legacy return values (a plain butler-name string) and the
        newer decomposition format (a list of ``{"butler": ..., "prompt": ...}``
        entries). Falls back to ``("general", fallback_message)`` when the
        payload is empty or invalid.
        """
        if isinstance(classification, str):
            target = classification.strip() or "general"
            return target, fallback_message

        if isinstance(classification, list) and classification:
            first = classification[0]
            if isinstance(first, dict):
                target = str(first.get("butler", "")).strip() or "general"
                routed_message = str(first.get("prompt", "")).strip() or fallback_message
                return target, routed_message

        return "general", fallback_message

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        if value in (None, ""):
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _source_endpoint_identity(
        cls,
        args: dict[str, Any],
        source_metadata: dict[str, str],
    ) -> str:
        explicit = cls._string_or_none(args.get("source_endpoint_identity"))
        if explicit is not None:
            return explicit
        channel = source_metadata.get("channel", "unknown")
        identity = source_metadata.get("identity", "unknown")
        return f"{channel}:{identity}"

    @classmethod
    def _source_sender_identity(
        cls,
        args: dict[str, Any],
        source_metadata: dict[str, str],
    ) -> str:
        candidates = (
            args.get("sender_identity"),
            args.get("from"),
            args.get("chat_id"),
            args.get("sender_id"),
            source_metadata.get("source_id"),
        )
        for candidate in candidates:
            normalized = cls._string_or_none(candidate)
            if normalized is not None:
                return normalized
        return "unknown"

    @classmethod
    def _source_thread_identity(cls, args: dict[str, Any]) -> str | None:
        candidates = (
            args.get("external_thread_id"),
            args.get("thread_id"),
            args.get("chat_id"),
            args.get("conversation_id"),
        )
        for candidate in candidates:
            normalized = cls._string_or_none(candidate)
            if normalized is not None:
                return normalized
        return None

    @classmethod
    def _external_event_id(
        cls,
        args: dict[str, Any],
        source_metadata: dict[str, str],
    ) -> str | None:
        candidates = (
            args.get("external_event_id"),
            args.get("message_id"),
            args.get("source_id"),
            source_metadata.get("source_id"),
        )
        for candidate in candidates:
            normalized = cls._string_or_none(candidate)
            if normalized is not None:
                return normalized
        return None

    @staticmethod
    def _window_bucket(received_at: datetime, *, minutes: int = 5) -> str:
        minute_bucket = (received_at.minute // minutes) * minutes
        bucket_start = received_at.replace(minute=minute_bucket, second=0, microsecond=0)
        return bucket_start.isoformat()

    @staticmethod
    def _payload_hash(payload: dict[str, Any]) -> str:
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @classmethod
    def _build_dedupe_record(
        cls,
        *,
        args: dict[str, Any],
        source_metadata: dict[str, str],
        message_text: str,
        received_at: datetime,
    ) -> tuple[str, str, str | None]:
        source_channel = source_metadata.get("channel", "unknown").strip().lower() or "unknown"
        endpoint_identity = cls._source_endpoint_identity(args, source_metadata)
        scoped_endpoint_identity = endpoint_identity
        if not scoped_endpoint_identity.startswith(f"{source_channel}:"):
            scoped_endpoint_identity = f"{source_channel}:{endpoint_identity}"
        external_event_id = cls._external_event_id(args, source_metadata)
        caller_idempotency_key = cls._string_or_none(
            args.get("idempotency_key") or args.get("ingress_idempotency_key")
        )

        if source_channel == "telegram" and external_event_id is not None:
            return (
                f"{scoped_endpoint_identity}:update:{external_event_id}",
                "telegram_update_id_endpoint",
                None,
            )

        if source_channel == "email" and external_event_id is not None:
            return (
                f"{scoped_endpoint_identity}:message_id:{external_event_id}",
                "email_message_id_endpoint",
                None,
            )

        if source_channel in {"api", "mcp"} and caller_idempotency_key is not None:
            return (
                f"{scoped_endpoint_identity}:idempotency:{caller_idempotency_key}",
                f"{source_channel}_idempotency_key_endpoint",
                caller_idempotency_key,
            )

        payload_for_hash = {
            "schema_version": "ingest.v1",
            "source_channel": source_channel,
            "source_endpoint_identity": scoped_endpoint_identity,
            "source_sender_identity": cls._source_sender_identity(args, source_metadata),
            "source_thread_identity": cls._source_thread_identity(args),
            "external_event_id": external_event_id,
            "message_text": message_text,
            "tool_name": source_metadata.get("tool_name"),
        }
        payload_hash = cls._payload_hash(payload_for_hash)
        bounded_window = cls._window_bucket(received_at)
        return (
            f"{scoped_endpoint_identity}:payload_hash:{payload_hash}:window:{bounded_window}",
            f"{source_channel}_payload_hash_endpoint_window",
            caller_idempotency_key,
        )

    async def _accept_ingress(
        self,
        *,
        message_text: str,
        args: dict[str, Any],
        source_metadata: dict[str, str],
        source: str,
        chat_id: str | None,
    ) -> _IngressDedupeRecord | None:
        if not self._enable_ingress_dedupe:
            return None

        received_at = datetime.now(UTC)
        dedupe_key, dedupe_strategy, idempotency_key = self._build_dedupe_record(
            args=args,
            source_metadata=source_metadata,
            message_text=message_text,
            received_at=received_at,
        )

        raw_metadata = args.get("raw_metadata")
        if isinstance(raw_metadata, dict):
            raw_metadata_payload: dict[str, Any] = dict(raw_metadata)
        else:
            raw_metadata_payload = {}
        raw_metadata_payload.setdefault("source_metadata", source_metadata)

        source_sender_identity = self._source_sender_identity(args, source_metadata)
        source_thread_identity = self._source_thread_identity(args)
        source_endpoint_identity = self._source_endpoint_identity(args, source_metadata)

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO message_inbox (
                    source_channel,
                    sender_id,
                    raw_content,
                    raw_metadata,
                    received_at,
                    source_endpoint_identity,
                    source_sender_identity,
                    source_thread_identity,
                    idempotency_key,
                    dedupe_key,
                    dedupe_strategy,
                    dedupe_last_seen_at
                ) VALUES (
                    $1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, $10, $11, $5
                )
                ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL DO UPDATE
                SET dedupe_last_seen_at = EXCLUDED.dedupe_last_seen_at
                RETURNING id AS request_id, (xmax = 0) AS inserted
                """,
                source,
                source_sender_identity,
                message_text,
                json.dumps(raw_metadata_payload, default=str),
                received_at,
                source_endpoint_identity,
                source_sender_identity,
                source_thread_identity,
                idempotency_key,
                dedupe_key,
                dedupe_strategy,
            )

        if row is None:
            return None

        request_id = row["request_id"]
        decision = "accepted" if bool(row["inserted"]) else "deduped"
        logger.info(
            "Ingress dedupe decision",
            extra=self._log_fields(
                source=source,
                chat_id=chat_id,
                target_butler=None,
                latency_ms=None,
                request_id=str(request_id),
                ingress_decision=decision,
                dedupe_key=dedupe_key,
                dedupe_strategy=dedupe_strategy,
            ),
        )
        return _IngressDedupeRecord(
            request_id=request_id,
            decision=decision,
            dedupe_key=dedupe_key,
            dedupe_strategy=dedupe_strategy,
        )

    @staticmethod
    def _json_param(payload: Any) -> str | None:
        import json

        if payload is None:
            return None
        return json.dumps(payload)

    async def _update_message_inbox_lifecycle(
        self,
        *,
        message_inbox_id: Any | None,
        decomposition_output: Any,
        dispatch_outcomes: Any,
        response_summary: str,
        lifecycle_state: str,
        classified_at: Any,
        classification_duration_ms: float,
        final_state_at: Any,
    ) -> None:
        import json

        if not message_inbox_id:
            return

        metadata = {
            "classified_at": classified_at.isoformat(),
            "classification_duration_ms": int(classification_duration_ms),
        }

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE message_inbox
                SET
                    decomposition_output = $1::jsonb,
                    dispatch_outcomes = $2::jsonb,
                    response_summary = $3,
                    lifecycle_state = $4,
                    final_state_at = $5,
                    processing_metadata = COALESCE(processing_metadata, '{}'::jsonb) || $6::jsonb,
                    updated_at = $7
                WHERE id = $8
                """,
                self._json_param(decomposition_output),
                self._json_param(dispatch_outcomes),
                response_summary,
                lifecycle_state,
                final_state_at,
                json.dumps(metadata),
                final_state_at,
                message_inbox_id,
            )

    async def process(
        self,
        message_text: str,
        tool_name: str = "bot_switchboard_handle_message",
        tool_args: dict[str, Any] | None = None,
        message_inbox_id: Any | None = None,
    ) -> RoutingResult:
        """Classify a message and route it to the appropriate butler.

        1. Calls ``classify_message()`` to determine the target butler.
        2. Calls ``route()`` to forward the message to that butler.

        Parameters
        ----------
        message_text:
            The raw message text to classify.
        tool_name:
            The MCP tool to invoke on the target butler.
        tool_args:
            Additional arguments to pass along with the message.
            The message text is always included as ``"message"``.
        message_inbox_id:
            The ID of the message in the message_inbox table.

        Returns
        -------
        RoutingResult
            Contains the target butler name, route result, and any errors.
        """
        from datetime import UTC, datetime

        # Lazy import to avoid circular dependencies
        from butlers.tools.switchboard import (
            aggregate_responses,
            classify_message,
            dispatch_decomposed,
            route,
        )

        classify = self._classify_fn or classify_message
        route_to = self._route_fn or route

        args = dict(tool_args or {})
        source_metadata = self._build_source_metadata(args, tool_name=tool_name)
        source = source_metadata["channel"]
        source_id = source_metadata.get("source_id")
        raw_chat_id = args.get("chat_id")
        chat_id = str(raw_chat_id) if raw_chat_id not in (None, "") else None
        message_length = len(message_text)
        message_preview = self._message_preview(message_text)

        start = time.perf_counter()
        logger.info(
            "Pipeline processing message",
            extra=self._log_fields(
                source=source,
                chat_id=chat_id,
                target_butler=None,
                latency_ms=0.0,
                message_length=message_length,
                message_preview=message_preview,
            ),
        )

        if message_inbox_id is None and self._enable_ingress_dedupe:
            try:
                ingress_record = await self._accept_ingress(
                    message_text=message_text,
                    args=args,
                    source_metadata=source_metadata,
                    source=source,
                    chat_id=chat_id,
                )
            except Exception:
                logger.exception(
                    "Ingress dedupe persistence failed; proceeding without dedupe",
                    extra=self._log_fields(
                        source=source,
                        chat_id=chat_id,
                        target_butler=None,
                        latency_ms=None,
                    ),
                )
                ingress_record = None

            if ingress_record is not None:
                message_inbox_id = ingress_record.request_id
                if ingress_record.decision == "deduped":
                    return RoutingResult(
                        target_butler="deduped",
                        route_result={
                            "request_id": str(ingress_record.request_id),
                            "ingress_decision": "deduped",
                            "dedupe_key": ingress_record.dedupe_key,
                            "dedupe_strategy": ingress_record.dedupe_strategy,
                        },
                    )

        # Step 1: Classify
        classify_start = time.perf_counter()
        classification = None
        try:
            classification = await classify(self._pool, message_text, self._dispatch_fn)
            classification_latency_ms = (time.perf_counter() - classify_start) * 1000

            # Handle decomposition (list of sub-tasks) only for default routing.
            # Custom route_fn call sites expect single-target routing semantics.
            if isinstance(classification, list) and classification and self._route_fn is None:
                logger.info(
                    "Pipeline classified message as decomposition",
                    extra=self._log_fields(
                        source=source,
                        chat_id=chat_id,
                        target_butler="multi",
                        latency_ms=classification_latency_ms,
                        subtask_count=len(classification),
                    ),
                )

                route_start = time.perf_counter()
                sub_results = await dispatch_decomposed(
                    self._pool,
                    targets=classification,
                    source_channel=source,
                    source_id=source_id,
                    tool_name=tool_name,
                    source_metadata=source_metadata,
                )
                routed_targets = [
                    str(entry.get("butler", "")).strip()
                    for entry in classification
                    if isinstance(entry, dict) and str(entry.get("butler", "")).strip()
                ]
                acked_targets: list[str] = []
                failed_targets: list[str] = []
                failed_details: list[str] = []
                for sub_result in sub_results:
                    butler = str(sub_result.get("butler", "")).strip()
                    if not butler:
                        continue
                    if sub_result.get("error"):
                        failed_targets.append(butler)
                        failed_details.append(f"{butler}: {sub_result['error']}")
                    else:
                        acked_targets.append(butler)

                aggregated = aggregate_responses(sub_results, dispatch_fn=self._dispatch_fn)
                if hasattr(aggregated, "__await__"):
                    aggregated = await aggregated

                routing_latency_ms = (time.perf_counter() - route_start) * 1000
                total_latency_ms = (time.perf_counter() - start) * 1000

                logger.info(
                    "Pipeline routed decomposed message",
                    extra=self._log_fields(
                        source=source,
                        chat_id=chat_id,
                        target_butler="multi",
                        latency_ms=total_latency_ms,
                        classification_latency_ms=classification_latency_ms,
                        routing_latency_ms=routing_latency_ms,
                    ),
                )

                completed_at = datetime.now(UTC)
                await self._update_message_inbox_lifecycle(
                    message_inbox_id=message_inbox_id,
                    decomposition_output=classification,
                    dispatch_outcomes=sub_results,
                    response_summary=str(aggregated),
                    lifecycle_state=("completed_with_failures" if failed_targets else "completed"),
                    classified_at=completed_at,
                    classification_duration_ms=classification_latency_ms,
                    final_state_at=completed_at,
                )

                return RoutingResult(
                    target_butler="multi",
                    route_result={"result": aggregated},
                    routing_error="; ".join(failed_details) if failed_details else None,
                    routed_targets=routed_targets,
                    acked_targets=acked_targets,
                    failed_targets=failed_targets,
                )

            target, routed_message = self._normalize_classification(
                classification,
                fallback_message=message_text,
            )
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            classification_latency_ms = (time.perf_counter() - classify_start) * 1000
            logger.warning(
                "Classification failed; falling back to general",
                extra=self._log_fields(
                    source=source,
                    chat_id=chat_id,
                    target_butler="general",
                    latency_ms=classification_latency_ms,
                    classification_error=error_msg,
                ),
            )

            completed_at = datetime.now(UTC)
            await self._update_message_inbox_lifecycle(
                message_inbox_id=message_inbox_id,
                decomposition_output={"error": error_msg},
                dispatch_outcomes=None,
                response_summary="Classification failed",
                lifecycle_state="classification_failed",
                classified_at=completed_at,
                classification_duration_ms=classification_latency_ms,
                final_state_at=completed_at,
            )

            return RoutingResult(
                target_butler="general",
                classification_error=error_msg,
            )
        classification_latency_ms = (time.perf_counter() - classify_start) * 1000
        args["prompt"] = routed_message
        args["message"] = routed_message
        args["source_metadata"] = source_metadata
        args["source_channel"] = source
        args["source_identity"] = source_metadata["identity"]
        args["source_tool"] = source_metadata["tool_name"]
        if source_id is not None:
            args["source_id"] = source_id
        logger.info(
            "Pipeline classified message",
            extra=self._log_fields(
                source=source,
                chat_id=chat_id,
                target_butler=target,
                latency_ms=classification_latency_ms,
            ),
        )

        # Step 2: Route
        route_start = time.perf_counter()
        result = None
        try:
            result = await route_to(
                self._pool,
                target,
                tool_name,
                args,
                self._source_butler,
            )
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            routing_latency_ms = (time.perf_counter() - route_start) * 1000
            logger.exception(
                "Routing failed for message to butler %s",
                target,
                extra=self._log_fields(
                    source=source,
                    chat_id=chat_id,
                    target_butler=target,
                    latency_ms=routing_latency_ms,
                    routing_error=error_msg,
                ),
            )

            completed_at = datetime.now(UTC)
            await self._update_message_inbox_lifecycle(
                message_inbox_id=message_inbox_id,
                decomposition_output=classification,
                dispatch_outcomes={"error": error_msg},
                response_summary="Routing failed",
                lifecycle_state="routing_failed",
                classified_at=completed_at,
                classification_duration_ms=classification_latency_ms,
                final_state_at=completed_at,
            )

            return RoutingResult(
                target_butler=target,
                routing_error=error_msg,
                routed_targets=[target],
                failed_targets=[target],
            )
        if isinstance(result, dict) and result.get("error"):
            error_msg = str(result["error"])
            routing_latency_ms = (time.perf_counter() - route_start) * 1000
            logger.warning(
                "Routing returned error for message to butler %s",
                target,
                extra=self._log_fields(
                    source=source,
                    chat_id=chat_id,
                    target_butler=target,
                    latency_ms=routing_latency_ms,
                    routing_error=error_msg,
                ),
            )

            completed_at = datetime.now(UTC)
            await self._update_message_inbox_lifecycle(
                message_inbox_id=message_inbox_id,
                decomposition_output=classification,
                dispatch_outcomes=result,
                response_summary="Routing failed",
                lifecycle_state="routing_failed",
                classified_at=completed_at,
                classification_duration_ms=classification_latency_ms,
                final_state_at=completed_at,
            )

            return RoutingResult(
                target_butler=target,
                route_result=result,
                routing_error=error_msg,
                routed_targets=[target],
                failed_targets=[target],
            )
        routing_latency_ms = (time.perf_counter() - route_start) * 1000
        total_latency_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Pipeline routed message",
            extra=self._log_fields(
                source=source,
                chat_id=chat_id,
                target_butler=target,
                latency_ms=total_latency_ms,
                classification_latency_ms=classification_latency_ms,
                routing_latency_ms=routing_latency_ms,
            ),
        )

        completed_at = datetime.now(UTC)
        await self._update_message_inbox_lifecycle(
            message_inbox_id=message_inbox_id,
            decomposition_output=classification,
            dispatch_outcomes=result,
            response_summary="Success",
            lifecycle_state="completed",
            classified_at=completed_at,
            classification_duration_ms=classification_latency_ms,
            final_state_at=completed_at,
        )

        return RoutingResult(
            target_butler=target,
            route_result=result,
            routed_targets=[target],
            acked_targets=[target],
        )
