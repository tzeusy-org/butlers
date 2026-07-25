"""Owner conditions broker module — the MCP doorway onto the owner condition ledger.

Registers the ``reconcile_owner_condition`` MCP tool on the Switchboard
butler (bu-ep4ks.6), mirroring ``InsightBrokerModule``'s shape exactly. An
LLM-driven butler session has no raw database pool of its own, so this tool
is the way such a session reconciles a standing owner-facing concern (an
overdue bill, a refill due, an expiring document, an overloaded day) against
``public.owner_conditions`` while staying MCP-only, consistent with the
schema-isolation model. Deterministic scheduled jobs (a
``dispatch_mode="job"`` handler with a raw ``asyncpg.Pool``, e.g.
``roster/finance/jobs/finance_jobs.py``) call
``butlers.core.owner_conditions.reconcile_snapshot`` directly and in-process
instead — the same split ``propose_insight_candidate`` already has between
its MCP tool (``InsightBrokerModule``) and its direct-import path.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from butlers.modules.base import Module

logger = logging.getLogger(__name__)


class OwnerConditionsBrokerConfig(BaseModel):
    """Configuration for the OwnerConditionsBrokerModule (no required settings)."""


class OwnerConditionsBrokerModule(Module):
    """Module that registers the reconcile_owner_condition MCP tool.

    Wires the Switchboard's owner-condition ledger into the MCP server so an
    LLM-driven butler session (not just a deterministic scheduled job) can
    open, confirm, escalate, or resolve a standing owner-facing concern
    against ``public.owner_conditions``.
    """

    def __init__(self) -> None:
        self._db: Any = None

    @property
    def name(self) -> str:
        return "owner_conditions_broker"

    @property
    def config_schema(self) -> type[BaseModel]:
        return OwnerConditionsBrokerConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        # public.owner_conditions is created by the shared core Alembic chain
        # (core_184) — no separate branch label needed here.
        return None

    async def on_startup(
        self,
        config: Any,
        db: Any,
        credential_store: Any = None,
        blob_store: Any = None,
    ) -> None:
        """Store the database reference for pool access at tool call time."""
        self._db = db

    async def on_shutdown(self) -> None:
        """Clear state references."""
        self._db = None

    def _get_pool(self) -> Any:
        """Return the asyncpg pool, raising if not initialised."""
        if self._db is None:
            raise RuntimeError("OwnerConditionsBrokerModule not initialised — no DB available")
        return self._db.pool

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        """Register the reconcile_owner_condition MCP tool."""
        self._db = db
        from butlers.core.owner_conditions import Observation, reconcile_snapshot

        @mcp.tool()
        async def reconcile_owner_condition(
            source: str,
            observations: list[dict[str, Any]],
            snapshot_complete: bool,
            initial_grace_seconds: float = 3600,
        ) -> dict[str, Any]:
            """Reconcile one producer check-in against the owner condition ledger.

            Opens, confirms, escalates, or resolves standing owner-facing
            concerns (an overdue bill, a refill due, an expiring document, an
            overloaded day) in ``public.owner_conditions`` — the same
            level-triggered open/aging/auto-resolve/re-escalate lifecycle
            ``infra_conditions`` uses for infrastructure reliability. This is
            a STATE ledger, not a delivery mechanism: reconciling a condition
            does not notify the owner by itself. Call
            ``propose_insight_candidate`` or ``notify()`` separately for
            delivery — gate delivery on this call's returned ``transition``
            (e.g. only deliver on ``"opened"``/``"reopened"``/
            ``"escalation_due"``, not on every ``"confirmed"``) to convert
            edge-and-forget re-firing into level-triggered attention.

            Parameters
            ----------
            source:
                Producer identity, by convention ``"{your_butler_name}:
                {category}"`` (e.g. ``"finance:bill-overdue"``). All active
                episodes for one ``source`` share one "complete snapshot"
                resolution scope and one advisory lock — never mix two
                unrelated concern categories under the same source string.
            observations:
                Every condition you currently observe for ``source``, each a
                dict with keys ``fingerprint`` (required — a stable identity
                string; the same underlying concern must always compute the
                same fingerprint), ``summary`` (optional human-readable
                evidence), and ``metadata`` (optional JSON-serializable dict).
                An empty list is valid (e.g. "nothing is overdue right now").
            snapshot_complete:
                True when ``observations`` is your FULL, successful
                enumeration of everything you currently observe for
                ``source`` — only then can an active episode absent from
                ``observations`` be resolved. Pass False for a partial or
                degraded check (never resolves anything by omission).
            initial_grace_seconds:
                Seconds before a newly-opened episode is first due for
                escalation (L0->L1). Defaults to one hour.

            Returns
            -------
            dict
                ``{"status": "accepted", "transitions": [...]}`` on success,
                each transition dict carrying ``fingerprint``, ``episode``,
                ``state``, ``transition`` (``"opened"``/``"reopened"``/
                ``"confirmed"``/``"escalation_due"``/``"resolved"``),
                ``escalation_level``, and (once resolved)
                ``recovered_after_s``. ``{"status": "error", "reason": "..."}``
                on validation failure (invalid input never reaches the pool).
            """
            try:
                parsed_observations = [
                    Observation(
                        fingerprint=o["fingerprint"],
                        summary=o.get("summary"),
                        metadata=o.get("metadata"),
                    )
                    for o in observations
                ]
            except KeyError:
                return {
                    "status": "error",
                    "reason": "each observation requires a 'fingerprint' field",
                }

            try:
                transitions = await reconcile_snapshot(
                    self._get_pool(),
                    source=source,
                    observations=parsed_observations,
                    snapshot_complete=snapshot_complete,
                    initial_grace_seconds=initial_grace_seconds,
                )
            except ValueError as exc:
                return {"status": "error", "reason": str(exc)}

            return {
                "status": "accepted",
                "transitions": [
                    {
                        "fingerprint": t.fingerprint,
                        "episode": t.episode,
                        "state": t.state,
                        "transition": t.transition,
                        "escalation_level": t.escalation_level,
                        "recovered_after_s": t.recovered_after_s,
                    }
                    for t in transitions
                ],
            }
