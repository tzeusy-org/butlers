"""Shared context object and shared types for core tool registration functions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, NotRequired, TypedDict

from pydantic import Field

if TYPE_CHECKING:

    pass


class NotifyRequestContextInput(TypedDict):
    """notify.request_context contract passed through to notify.v1."""

    request_id: Annotated[str, Field(description="UUID7 request ID from REQUEST CONTEXT.")]
    source_channel: Annotated[
        str, Field(description="Source channel from REQUEST CONTEXT (for example telegram).")
    ]
    source_endpoint_identity: Annotated[
        str, Field(description="Source endpoint identity from REQUEST CONTEXT.")
    ]
    source_sender_identity: Annotated[
        str, Field(description="Source sender identity from REQUEST CONTEXT.")
    ]
    source_thread_identity: NotRequired[
        Annotated[
            str,
            Field(
                description=(
                    "Required for telegram reply/react intents; identifies the source thread/chat."
                )
            ),
        ]
    ]
    received_at: NotRequired[
        Annotated[str, Field(description="Optional RFC3339 source receive timestamp.")]
    ]


@dataclass
class ToolContext:
    """Bundles all shared state captured by _register_core_tools() closures.

    This is passed to every ``register_*_tools(ctx, mcp, _core_tool)`` function
    so that extracted tool handlers have the same access to daemon internals as
    the original closure-based handlers.

    Fields mirror the locals set up at the top of ``_register_core_tools()``.
    """

    # -- daemon reference (used extensively for health, modules, spawner, etc.)
    daemon: Any  # ButlerDaemon (avoid circular import)

    # -- DB pool (most tools need this)
    pool: Any  # asyncpg.Pool | None

    # -- LLM spawner
    spawner: Any  # Spawner

    # -- Butler identity
    butler_name: str
    butler_type: Any  # ButlerType

    # -- Derived flags
    is_switchboard: bool
    is_messenger: bool

    # -- Metrics instance
    route_metrics: Any  # ButlerMetrics

    # -- Additional context (populated as needed)
    extra: dict[str, Any] = field(default_factory=dict)
