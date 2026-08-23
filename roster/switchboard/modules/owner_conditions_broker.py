"""Owner conditions broker module — the MCP doorway onto the owner condition ledger.

Registers the ``reconcile_owner_condition`` (bu-ep4ks.6) and
``resolve_owner_condition`` (bu-vdv7j, REQ-owner-condition-ledger-005) MCP
tools on the Switchboard butler, mirroring ``InsightBrokerModule``'s shape
exactly. An
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

from butlers.core.tool_call_capture import get_current_runtime_session_id
from butlers.modules.base import Module

logger = logging.getLogger(__name__)

# The resolution vocabulary REQ-owner-condition-ledger-005 allows. Kept as a
# runtime-checked tuple rather than a ``Literal`` annotation on the tool
# signature on purpose: a ``Literal`` would make FastMCP reject the call at the
# schema boundary, whereas the spec requires the tool itself to answer an
# unknown reason with ``{"status": "error", "reason": ...}`` and no DB write.
RESOLUTION_REASONS: tuple[str, ...] = ("satisfied", "cancelled", "superseded", "expired")


class OwnerConditionsBrokerConfig(BaseModel):
    """Configuration for the OwnerConditionsBrokerModule (no required settings)."""


class OwnerConditionsBrokerModule(Module):
    """Module that registers the owner-condition ledger MCP tools.

    Wires the Switchboard's owner-condition ledger into the MCP server so an
    LLM-driven butler session (not just a deterministic scheduled job) can
    open, confirm, escalate, or resolve a standing owner-facing concern
    against ``public.owner_conditions``: ``reconcile_owner_condition`` for
    level-triggered snapshot reconciliation, and ``resolve_owner_condition``
    for explicit, snapshot-free closure of one known identity.
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
        """Register the reconcile_owner_condition and resolve_owner_condition MCP tools."""
        self._db = db
        from butlers.core.owner_conditions import Observation, reconcile_snapshot, resolve_condition

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
                ``metadata`` may not carry the top-level keys
                ``resolution_reason`` or ``evidence_closed``: those are the
                ledger's, written when the condition is resolved, and a
                snapshot claiming one is rejected without a database write.
                Record why you expect the condition to close under a name of
                your own.
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

        @mcp.tool()
        async def resolve_owner_condition(
            source: str,
            fingerprint: str,
            resolution_reason: str,
            resolution_detail: str | None = None,
        ) -> dict[str, Any]:
            """Explicitly resolve one active owner condition you know is closed.

            Use this when the owner (or unambiguous evidence in your session)
            tells you a standing concern is finished — the bill was paid, the
            promise was kept, the plan was cancelled — and you are NOT in a
            position to enumerate everything you observe for ``source``.
            ``reconcile_owner_condition`` resolves by omission from a complete
            snapshot, which a conversational session cannot honestly produce;
            this tool closes exactly one identity and touches nothing else.

            Like ``reconcile_owner_condition`` this is a STATE ledger write,
            not a delivery mechanism: resolving a condition does not tell the
            owner anything. It is idempotent — resolving an already-resolved
            or never-seen identity is a harmless ``"not_found"``.

            Parameters
            ----------
            source:
                The producer identity the condition was opened under (e.g.
                ``"finance:bill-overdue"``). Must match exactly; resolution is
                scoped to one ``(source, fingerprint)`` pair.
            fingerprint:
                The stable identity string of the condition to close, as
                returned by ``reconcile_owner_condition`` or listed by the
                ledger. Must match exactly.
            resolution_reason:
                Why the condition is closing. One of ``"satisfied"`` (the
                concern was actually addressed), ``"cancelled"`` (it no longer
                applies), ``"superseded"`` (another condition replaced it), or
                ``"expired"`` (its window passed without action). Any other
                value is rejected before any database access.
            resolution_detail:
                Optional free-text evidence for the closure (e.g. "owner
                confirmed the transfer cleared on the 4th"), stored alongside
                the resolution for later audit.

            Returns
            -------
            dict
                ``{"status": "resolved", "episode": <n>, "fingerprint": "...",
                "resolution_reason": "..."}`` when an active episode was
                closed; ``{"status": "not_found"}`` when the identity has no
                active ``open``/``aging`` episode (never observed, or already
                resolved); ``{"status": "error", "reason": "..."}`` on
                validation failure, which never reaches the pool.

            Notes
            -----
            The closing evidence is merged into the row's existing metadata
            with creation-wins semantics, so every top-level key the producer
            set at creation time keeps its original value. The two keys this
            writes — ``resolution_reason`` and ``evidence_closed`` — are
            reserved at the ``reconcile_owner_condition`` boundary precisely
            so creation-wins can never apply to them: the closing evidence,
            session id included, always lands.
            """
            if resolution_reason not in RESOLUTION_REASONS:
                return {
                    "status": "error",
                    "reason": (
                        "resolution_reason must be one of "
                        f"{', '.join(RESOLUTION_REASONS)}; got {resolution_reason!r}"
                    ),
                }

            resolution_metadata: dict[str, Any] = {
                "resolution_reason": resolution_reason,
                "evidence_closed": {
                    "source": "owner_confirmed",
                    "detail": resolution_detail,
                    "session_id": get_current_runtime_session_id(),
                },
            }

            try:
                transition = await resolve_condition(
                    self._get_pool(),
                    source=source,
                    fingerprint=fingerprint,
                    resolution_metadata=resolution_metadata,
                )
            except ValueError as exc:
                return {"status": "error", "reason": str(exc)}

            if transition is None:
                return {"status": "not_found"}

            return {
                "status": "resolved",
                "episode": transition.episode,
                "fingerprint": transition.fingerprint,
                "resolution_reason": resolution_reason,
            }
