"""Message classification and routing pipeline for input modules.

Provides a ``MessagePipeline`` that connects input modules (Telegram, Email)
to the switchboard's ``classify_message()`` and ``route()`` functions.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RoutingResult:
    """Result of classifying and routing a message through the pipeline."""

    target_butler: str
    route_result: dict[str, Any] = field(default_factory=dict)
    classification_error: str | None = None
    routing_error: str | None = None


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
    ) -> None:
        self._pool = switchboard_pool
        self._dispatch_fn = dispatch_fn
        self._source_butler = source_butler
        self._classify_fn = classify_fn
        self._route_fn = route_fn

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

    async def process(
        self,
        message_text: str,
        tool_name: str = "handle_message",
        tool_args: dict[str, Any] | None = None,
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

        Returns
        -------
        RoutingResult
            Contains the target butler name, route result, and any errors.
        """
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

        source = str(args.get("source") or "unknown")
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

        # Step 1: Classify
        classify_start = time.perf_counter()
        try:
            classification = await classify(self._pool, message_text, self._dispatch_fn)

            # Handle decomposition (list of sub-tasks)
            if isinstance(classification, list) and classification:
                # We need source_id from args if available for tracing
                source_id = str(args.get("source_id")) if args.get("source_id") else None

                classification_latency_ms = (time.perf_counter() - classify_start) * 1000
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
                )
                
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

                return RoutingResult(
                    target_butler="multi",
                    route_result={"result": aggregated},
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
            return RoutingResult(
                target_butler="general",
                classification_error=error_msg,
            )
        classification_latency_ms = (time.perf_counter() - classify_start) * 1000
        args["message"] = routed_message
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
            return RoutingResult(
                target_butler=target,
                routing_error=error_msg,
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

        return RoutingResult(
            target_butler=target,
            route_result=result,
        )
