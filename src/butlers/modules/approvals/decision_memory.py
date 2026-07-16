"""Deterministic decision facts stored through an owning memory module.

The approvals audit tables remain the event spine.  This module writes the
small, recall-worthy summaries that let a later session understand the owner's
revealed preferences without asking an LLM to infer them again.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from butlers.modules.approvals.autonomy_tracker import (
    FINGERPRINT_VERSION,
    compute_fingerprint,
    fingerprinted_args,
)
from butlers.modules.memory.storage import store_fact
from butlers.modules.memory.tools.writing import normalize_predicate

if TYPE_CHECKING:
    from butlers.modules.base import ToolMeta

logger = logging.getLogger(__name__)

_TALLY_PREDICATE = normalize_predicate("decision:approval_tally")
_RULE_PREDICATE = normalize_predicate("decision:standing_rule")
_TERMINAL_OUTCOMES = frozenset({"approved", "rejected"})


def _canonical_json(value: Any) -> str:
    """Serialize a deterministic, compact description without relying on an LLM."""
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


def _pattern_descriptor(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_meta: ToolMeta | None,
) -> str:
    """Return the human-readable descriptor for the fingerprinted action pattern."""
    return f"{tool_name}({_canonical_json(fingerprinted_args(tool_args, tool_meta))})"


def _tally_scope(butler_name: str, fingerprint: str) -> str:
    """Give each fingerprint an independent stable-fact key in its own namespace."""
    return f"{butler_name}:decision:{fingerprint}"


def _rule_scope(butler_name: str, rule_id: Any) -> str:
    """Give each standing rule a stable-fact key that can be superseded on revoke."""
    return f"{butler_name}:decision:rule:{rule_id}"


def _metadata_mapping(value: Any) -> dict[str, Any]:
    """Normalize asyncpg JSONB values while preserving malformed rows as empty."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


async def _existing_tally_metadata(
    memory_pool: Any,
    *,
    scope: str,
    subject: str,
    entity_id: Any,
) -> dict[str, Any]:
    """Read the live tally metadata before deterministic replacement/supersession."""
    if entity_id is not None:
        row = await memory_pool.fetchrow(
            "SELECT metadata FROM facts "
            "WHERE tenant_id = $1 AND entity_id = $2 AND object_entity_id IS NULL "
            "AND scope = $3 AND predicate = $4 "
            "AND validity IN ('active', 'fading') AND valid_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            "shared",
            entity_id,
            scope,
            _TALLY_PREDICATE,
        )
    else:
        row = await memory_pool.fetchrow(
            "SELECT metadata FROM facts "
            "WHERE tenant_id = $1 AND entity_id IS NULL AND scope = $2 "
            "AND subject = $3 AND predicate = $4 "
            "AND validity IN ('active', 'fading') AND valid_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            "shared",
            scope,
            subject,
            _TALLY_PREDICATE,
        )
    return _metadata_mapping(row["metadata"]) if row is not None else {}


def _count(previous: dict[str, Any], key: str) -> int:
    """Return a non-negative tally value from a prior fact's metadata."""
    value = previous.get(key, 0)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


class DecisionMemoryWriter:
    """Best-effort writer bound to one approvals module and its own memory store.

    The providers intentionally defer access to the MemoryModule's pool and
    embedding engine until a terminal state is committed.  An absent memory
    module is represented by not constructing this writer at all; failures
    after construction are logged but never affect the decision result.
    """

    def __init__(
        self,
        *,
        butler_name: str,
        memory_pool_provider: Callable[[], Any],
        resolution_pool_provider: Callable[[], Any],
        embedding_engine_provider: Callable[[], Any],
        tool_meta_provider: Callable[[str], ToolMeta | None],
    ) -> None:
        self._butler_name = butler_name
        self._memory_pool_provider = memory_pool_provider
        self._resolution_pool_provider = resolution_pool_provider
        self._embedding_engine_provider = embedding_engine_provider
        self._tool_meta_provider = tool_meta_provider

    async def record_terminal_decision(self, action: Any, outcome: str) -> None:
        """Write a tally after a committed rejection or execution outcome.

        This method is deliberately fail-open: the action's terminal state and
        audit event have already been persisted before it is called.
        """
        if outcome not in _TERMINAL_OUTCOMES:
            logger.warning(
                "Decision-memory writeback ignored unsupported terminal outcome %r for action %s",
                outcome,
                getattr(action, "id", "unknown"),
            )
            return

        try:
            await self._write_terminal_decision(action, outcome)
        except Exception:  # noqa: BLE001 -- writeback must never reverse a decision
            logger.warning(
                "Decision-memory tally writeback failed for action %s; "
                "terminal decision remains committed",
                getattr(action, "id", "unknown"),
                exc_info=True,
            )

    async def record_standing_rule(self, rule: Any, *, active: bool) -> None:
        """Write the active/revoked descriptive fact for a standing rule.

        As with tally writes, failures are observational and cannot block rule
        creation or revocation.
        """
        try:
            await self._write_standing_rule(rule, active=active)
        except Exception:  # noqa: BLE001 -- writeback must never reverse a rule state
            logger.warning(
                "Decision-memory standing-rule writeback failed for rule %s; "
                "rule state remains committed",
                getattr(rule, "id", "unknown"),
                exc_info=True,
            )

    async def _resources(self) -> tuple[Any, Any, Any]:
        """Resolve owning pools and lazily obtain the memory embedding engine."""
        memory_pool = self._memory_pool_provider()
        resolution_pool = self._resolution_pool_provider()
        embedding_engine = await asyncio.to_thread(self._embedding_engine_provider)
        return memory_pool, resolution_pool, embedding_engine

    async def _write_terminal_decision(self, action: Any, outcome: str) -> None:
        memory_pool, resolution_pool, embedding_engine = await self._resources()
        tool_meta = self._tool_meta_provider(action.tool_name)
        fingerprint = compute_fingerprint(action.tool_name, action.tool_args, tool_meta=tool_meta)
        subject = _pattern_descriptor(action.tool_name, action.tool_args, tool_meta)
        scope = _tally_scope(self._butler_name, fingerprint)

        # Reuse the approval gate's established channel/entity resolution rather
        # than duplicating identity interpretation in a new writer.
        from butlers.modules.approvals.gate import _resolve_target_contact

        resolved_target = await _resolve_target_contact(resolution_pool, action.tool_args)
        entity_id = resolved_target.entity_id if resolved_target is not None else None
        previous = await _existing_tally_metadata(
            memory_pool,
            scope=scope,
            subject=subject,
            entity_id=entity_id,
        )

        approve_count = _count(previous, "approve_count")
        reject_count = _count(previous, "reject_count")
        if outcome == "approved":
            approve_count += 1
        else:
            reject_count += 1

        metadata = {
            "approve_count": approve_count,
            "reject_count": reject_count,
            "last_decision": outcome,
            "last_action_id": str(action.id),
            "fingerprint": fingerprint,
            "fingerprint_version": FINGERPRINT_VERSION,
        }
        content = (
            f"Owner {outcome} this action pattern. "
            f"Approved {approve_count} time(s); rejected {reject_count} time(s)."
        )
        await store_fact(
            memory_pool,
            subject=subject,
            predicate=_TALLY_PREDICATE,
            content=content,
            embedding_engine=embedding_engine,
            importance=8.0,
            permanence="stable",
            scope=scope,
            tags=["decision", "approval"],
            source_butler=self._butler_name,
            metadata=metadata,
            entity_id=entity_id,
            retention_class="operational",
            sensitivity="normal",
        )

    async def _write_standing_rule(self, rule: Any, *, active: bool) -> None:
        memory_pool, _resolution_pool, embedding_engine = await self._resources()
        state = "active" if active else "revoked"
        constraints = dict(rule.arg_constraints)
        metadata = {
            "rule_id": str(rule.id),
            "tool_name": rule.tool_name,
            "arg_constraints": constraints,
            "state": state,
        }
        content = (
            f"Standing approval rule is {state} for tool {rule.tool_name}. "
            f"It has {len(constraints)} pinned constraint(s); its durable rule identifier "
            "and constraints are recorded in metadata."
        )
        await store_fact(
            memory_pool,
            subject=f"Standing approval rule for {rule.tool_name}",
            predicate=_RULE_PREDICATE,
            content=content,
            embedding_engine=embedding_engine,
            importance=8.0,
            permanence="stable",
            scope=_rule_scope(self._butler_name, rule.id),
            tags=["decision", "approval", "standing-rule"],
            source_butler=self._butler_name,
            metadata=metadata,
            retention_class="operational",
            sensitivity="normal",
        )
