"""Per-task routing context variable for the message pipeline.

Provides the ContextVar that carries switchboard routing context into spawned
LLM CLI sessions.  This lives in ``core`` because it is read by ``core.spawner``
and ``core_tools._routing``; the ``modules.pipeline`` package *writes* to it
during ``MessagePipeline.process()``.

Placing the ContextVar here keeps the dependency direction clean:

    modules.pipeline  →  core.routing_context  (module depends on core ✓)
    core.spawner      →  core.routing_context  (core-internal ✓)
    core_tools.*      →  core.routing_context  (core_tools depend on core ✓)
"""

from __future__ import annotations

import contextvars
from typing import Any

# Per-task routing context for concurrent pipeline sessions.
# Each asyncio task (pipeline.process() call) sets its own isolated copy,
# preventing cross-contamination when max_concurrent_sessions > 1.
_routing_ctx_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "_routing_ctx_var", default=None
)
