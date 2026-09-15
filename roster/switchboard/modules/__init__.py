"""Switchboard roster modules.

Exports all concrete Module subclasses discovered by the ModuleRegistry scanner.
Each module is implemented in a dedicated sub-module:

- ``SwitchboardModule``    — routing, operator controls, notification delivery,
                            extraction audit, backfill, and dead-letter tools.
- ``InsightBrokerModule`` — proactive insight candidate submission tool
                            (``propose_insight_candidate``).
- ``OwnerConditionsBrokerModule`` — owner condition ledger tools
                            (``reconcile_owner_condition`` for snapshot
                            reconciliation, ``resolve_owner_condition`` for
                            explicit closure of one identity).

The tool closures strip infrastructure arguments (pool, conn) from the
MCP-visible signature and inject them from module state at call time.

The Switchboard is an infrastructure butler. Many of its tools take either
``pool: asyncpg.Pool`` or ``conn: asyncpg.Connection`` as the first argument.
For conn-based tools, the module acquires a connection from the pool within
the closure.

Internal daemon infrastructure functions (ingest pipeline, heartbeat ingestion,
triage evaluation, eligibility sweeps, identity resolution, connector-facing
backfill tools, telemetry, and parse/validation utilities) are NOT registered
as MCP tools — they are called directly by the daemon or connectors.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import uuid
from typing import Any

from pydantic import BaseModel

from butlers.credential_store import resolve_owner_telegram_recipient
from butlers.modules.base import Module, ToolGroupMixin, ToolMeta
from butlers.tools.switchboard.runtime_attention.outbox import RuntimeAttentionOutbox
from butlers.tools.switchboard.runtime_attention.worker import (
    RuntimeAttentionDeliveryWorker,
    build_messenger_transport,
)

from .insight_broker import InsightBrokerConfig, InsightBrokerModule  # noqa: F401
from .owner_conditions_broker import (  # noqa: F401
    OwnerConditionsBrokerConfig,
    OwnerConditionsBrokerModule,
)

logger = logging.getLogger(__name__)

# Runtime attention is an alerting path (breaker-open, fleet-halt): poll
# frequently rather than on the calendar-sync scale of minutes.
_RUNTIME_ATTENTION_POLL_INTERVAL_SECONDS = 10


__all__ = [
    "InsightBrokerConfig",
    "InsightBrokerModule",
    "OwnerConditionsBrokerConfig",
    "OwnerConditionsBrokerModule",
    "SwitchboardModule",
    "SwitchboardModuleConfig",
]


class SwitchboardModuleConfig(ToolGroupMixin, BaseModel):
    """Configuration for the Switchboard module.

    Tool groups
    -----------
    routing : list_butlers, route, post_mail, correct_route, deliver
    lifecycle : connector_disconnect
    extraction : log_extraction, extraction_log_list, extraction_log_undo
    backfill : create_backfill_job, backfill_pause, backfill_cancel,
               backfill_resume, backfill_list
    operator : manual_reroute_request, cancel_request, abort_request,
               force_complete_request, replay_dead_letter_request,
               list_replay_eligible_requests, get_dead_letter_stats
    """


class SwitchboardModule(Module):
    """Switchboard module providing MCP tools for routing, operator controls,
    notification delivery, extraction audit, backfill management, and dead-letter
    queue operations.
    """

    def __init__(self) -> None:
        self._db: Any = None
        self._runtime_attention_task: asyncio.Task[None] | None = None

    @property
    def name(self) -> str:
        return "switchboard"

    @property
    def config_schema(self) -> type[BaseModel]:
        return SwitchboardModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None  # switchboard tables already exist via separate migrations

    def tool_metadata(self) -> dict[str, ToolMeta]:
        """Declare the connector identity as safety-critical for approval rules."""
        return {
            "connector_disconnect": ToolMeta(
                arg_sensitivities={
                    "connector_type": True,
                    "endpoint_identity": True,
                }
            )
        }

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        """Store the Database reference and start the runtime-attention delivery worker.

        Vision Rule 3 / RFC 0003 make Switchboard the sole boundary that may put
        a runtime-attention episode (breaker-open, fleet-halt) in front of the
        operator. The worker itself (``RuntimeAttentionDeliveryWorker``) has been
        buildable since bu-0uqgo.3/.6; this is the construction site that
        activates it.
        """
        self._db = db
        pool = db.pool
        repository = RuntimeAttentionOutbox(pool, instance_id=str(uuid.uuid4()))
        transport = build_messenger_transport(
            pool,
            resolve_recipient=functools.partial(resolve_owner_telegram_recipient, pool),
        )
        worker = RuntimeAttentionDeliveryWorker(repository, transport)
        self._runtime_attention_task = asyncio.create_task(
            self._run_runtime_attention_worker(worker),
            name="switchboard-runtime-attention-delivery",
        )
        logger.info(
            "Runtime-attention delivery worker started (poll_interval=%ds)",
            _RUNTIME_ATTENTION_POLL_INTERVAL_SECONDS,
        )

    async def on_shutdown(self) -> None:
        """Clear state references and stop the runtime-attention delivery worker."""
        if self._runtime_attention_task is not None and not self._runtime_attention_task.done():
            self._runtime_attention_task.cancel()
            try:
                await self._runtime_attention_task
            except asyncio.CancelledError:
                pass
        self._runtime_attention_task = None
        self._db = None

    async def _run_runtime_attention_worker(self, worker: RuntimeAttentionDeliveryWorker) -> None:
        """Background task: drive the runtime-attention outbox at a fixed poll interval.

        Errors are caught and logged rather than left to kill the task: a
        transient DB or transport failure must not silence attention delivery
        until the next daemon restart.
        """
        while True:
            try:
                await worker.run_once()
            except Exception:
                logger.exception("Runtime-attention delivery worker pass failed")
            try:
                await asyncio.sleep(_RUNTIME_ATTENTION_POLL_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break

    def _get_pool(self):
        """Return the asyncpg pool, raising if not initialised."""
        if self._db is None:
            raise RuntimeError("SwitchboardModule not initialised — no DB available")
        return self._db.pool

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        """Register all switchboard MCP tools."""
        self._db = db
        from .tools import register_tools

        register_tools(mcp, self, config=config)
