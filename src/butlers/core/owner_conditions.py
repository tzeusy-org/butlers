"""The owner condition ledger — durable append-per-episode owner-facing standing concerns.

bu-ep4ks.6 (2026-07-25 JARVIS pursuit, rank 6) — generalizes
``butlers.core.infra_conditions`` (bu-27dxl.6.2) beyond infrastructure
reliability. That module proved durable open/aging/auto-resolve/re-escalate
semantics for a producer's standing evidence, but its docstring scoped it to
infrastructure (deploy drift, calendar sync deadman). Owner-facing standing
concerns — an overdue bill, a refill due, an expiring document, an
overloaded day — existed only as re-fired ``insight_candidates`` suppressed
by ``cooldown_days`` (``butlers.tools.switchboard.insight.broker``): a
producer re-proposes the same candidate on a fixed cadence regardless of
whether the underlying concern is still true, which structurally cannot
express "still true and still unactioned" (there is no durable state to
query — only a delivery-dedup timer). This module gives that state a home:
the exact same level-triggered lifecycle machinery infra_conditions
hardened, reused verbatim via ``butlers.core.condition_ledger``, against a
dedicated ``public.owner_conditions`` table so an owner-facing standing
concern's lifecycle is never confused with — or dependent on — an
infrastructure reliability episode.

Relationship to the insight broker (``propose_insight_candidate``)
--------------------------------------------------------------------------
This module is a STATE ledger, not a DELIVERY mechanism. Reconciling a
condition here does not send anything to the owner by itself; a producer
that wants delivery still calls ``propose_insight_candidate`` (or
``notify()``) as it always has. What changes is that the producer now also
has a durable, queryable answer to "is this concern still open, and for how
long, and at what escalation level" that a one-shot dedup timer cannot give
— visible on the dashboard's Standing Conditions panel (``GET
/api/system/conditions?source=...``) alongside infrastructure conditions,
and available for a producer to gate delivery on a *transition* (opened,
reopened, escalation_due) rather than re-firing on every cooldown expiry
regardless of whether anything changed.

``source`` naming convention
-----------------------------
Mirrors infra_conditions' per-producer source strings (e.g.
``"deploy_drift"``, ``"calendar_sync_deadman"``): an owner-conditions
producer names its source as ``"{origin_butler}:{category}"`` (e.g.
``"finance:bill-overdue"``, ``"finance:spending-anomaly"``) so the dashboard
can group/filter by producer without a separate schema column, and so two
categories from the same butler never contend on the same advisory lock or
"complete snapshot" resolution scope — a ``snapshot_complete=True`` call for
``"finance:bill-overdue"`` only ever resolves overdue-bill episodes, never a
spending-anomaly episode also produced by the finance butler.

MCP surface
-----------
Scheduled/deterministic jobs (e.g. ``roster/finance/jobs/finance_jobs.py``,
a ``dispatch_mode="job"`` handler with a raw ``asyncpg.Pool``) call
``reconcile_snapshot`` below directly and in-process — the same pattern
those jobs already use for ``propose_insight_candidate`` (a plain Python
import, no MCP round-trip: ``public`` is readable/writable by every butler
role per the schema-isolation model). An LLM-driven butler session has no
such raw pool; for it, the Switchboard's ``reconcile_owner_condition`` and
``resolve_owner_condition`` MCP tools
(``roster/switchboard/modules/owner_conditions_broker.py``) are the doorway,
consistent with "butlers stay MCP-only" for interactive/session code —
mirroring how ``propose_insight_candidate`` is itself exposed as an MCP tool
alongside its direct-import path. A conversational session cannot honestly
produce a complete snapshot, so ``resolve_condition`` below (and the
``resolve_owner_condition`` tool over it) is how such a session closes one
known identity without resolving anything else by omission.

See ``butlers.core.condition_ledger`` for the full lifecycle contract this
module implements (identity fingerprinting, concurrency, snapshot-complete
resolution) — every function below is a thin facade binding
``table="public.owner_conditions"``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import asyncpg

from butlers.core.condition_ledger import ESCALATION_LEVELS as ESCALATION_LEVELS
from butlers.core.condition_ledger import VALID_STATES as VALID_STATES
from butlers.core.condition_ledger import ConditionTransition, Observation
from butlers.core.condition_ledger import TransitionKind as TransitionKind
from butlers.core.condition_ledger import compute_fingerprint as compute_fingerprint
from butlers.core.condition_ledger import get_active_condition as _get_active_condition
from butlers.core.condition_ledger import list_conditions as _list_conditions
from butlers.core.condition_ledger import reconcile_snapshot as _reconcile_snapshot
from butlers.core.condition_ledger import resolve_condition as _resolve_condition

__all__ = [
    "ESCALATION_LEVELS",
    "VALID_STATES",
    "ConditionTransition",
    "Observation",
    "TransitionKind",
    "compute_fingerprint",
    "create_owner_conditions_table",
    "get_active_condition",
    "list_conditions",
    "reconcile_snapshot",
    "resolve_condition",
]

_TABLE = "public.owner_conditions"


async def reconcile_snapshot(
    pool: asyncpg.Pool,
    *,
    source: str,
    observations: Sequence[Observation],
    snapshot_complete: bool,
    initial_grace_seconds: float,
) -> list[ConditionTransition]:
    """Atomically reconcile one producer check-in against the owner condition ledger.

    See ``butlers.core.condition_ledger.reconcile_snapshot`` for the full
    contract. This facade binds ``table="public.owner_conditions"``.
    ``source`` should follow the ``"{origin_butler}:{category}"`` convention
    documented in this module's docstring.
    """
    return await _reconcile_snapshot(
        pool,
        table=_TABLE,
        source=source,
        observations=observations,
        snapshot_complete=snapshot_complete,
        initial_grace_seconds=initial_grace_seconds,
    )


async def resolve_condition(
    pool: asyncpg.Pool,
    *,
    source: str,
    fingerprint: str,
    resolution_metadata: dict[str, Any] | None = None,
) -> ConditionTransition | None:
    """Explicitly resolve an active owner condition.

    This thin facade binds the shared condition-ledger resolver to
    ``table="public.owner_conditions"``. Resolution metadata is shallowly
    merged with creation-wins semantics, so existing top-level metadata values
    are retained while new closing evidence can be added.
    """
    return await _resolve_condition(
        pool,
        table=_TABLE,
        source=source,
        fingerprint=fingerprint,
        resolution_metadata=resolution_metadata,
    )


async def get_active_condition(
    pool: asyncpg.Pool, *, source: str, fingerprint: str
) -> dict[str, Any] | None:
    """Return the active (``open``/``aging``) episode for ``(source, fingerprint)``.

    Returns ``None`` when there is no active episode — either the identity
    has never been observed, or its most recent episode already resolved.
    """
    return await _get_active_condition(pool, table=_TABLE, source=source, fingerprint=fingerprint)


async def list_conditions(
    pool: asyncpg.Pool,
    *,
    source: str | None = None,
    state: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[int, list[dict[str, Any]]]:
    """List condition-ledger episodes, most-recently-detected first.

    Returns ``(total, rows)`` where ``total`` is the unfiltered-by-page count
    matching the given filters (for pagination), and ``rows`` is the current
    page ordered by ``first_detected_at DESC``.
    """
    return await _list_conditions(
        pool, table=_TABLE, source=source, state=state, offset=offset, limit=limit
    )


async def create_owner_conditions_table(pool: asyncpg.Pool) -> None:
    """Create ``public.owner_conditions`` for use in tests with an isolated pool.

    In production this table is created via the core Alembic migration
    (``core_184_owner_conditions``) — this mirrors that DDL exactly (minus
    role grants, which are meaningless against a throwaway test pool) so
    tests using an unmigrated pool (e.g. ``roster/finance/tests/test_jobs.py``,
    which provisions the finance schema by hand rather than via
    ``create_migrated_test_db``) can provision it without drifting from the
    real schema — mirrors ``butlers.tools.switchboard.insight.broker.
    create_insight_tables``' role for ``insight_candidates``.
    """
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS public.owner_conditions (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source              TEXT NOT NULL,
            fingerprint         TEXT NOT NULL,
            episode             INTEGER NOT NULL,
            state               TEXT NOT NULL DEFAULT 'open',
            first_detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_confirmed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_escalated_at   TIMESTAMPTZ,
            next_reescalate_at  TIMESTAMPTZ,
            escalation_level    TEXT NOT NULL DEFAULT 'L0',
            resolved_at         TIMESTAMPTZ,
            recovered_after_s   DOUBLE PRECISION,
            summary             TEXT,
            metadata            JSONB,
            CONSTRAINT chk_owner_conditions_state
                CHECK (state IN ('open', 'aging', 'resolved')),
            CONSTRAINT chk_owner_conditions_escalation_level
                CHECK (escalation_level IN ('L0', 'L1', 'L2', 'L3')),
            CONSTRAINT chk_owner_conditions_episode_positive
                CHECK (episode >= 1),
            CONSTRAINT chk_owner_conditions_resolved_fields
                CHECK (
                    (state = 'resolved' AND resolved_at IS NOT NULL
                        AND recovered_after_s IS NOT NULL)
                    OR
                    (state != 'resolved' AND resolved_at IS NULL
                        AND recovered_after_s IS NULL)
                )
        )
    """)
    await pool.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_owner_conditions_active_episode
        ON public.owner_conditions (source, fingerprint)
        WHERE state IN ('open', 'aging')
    """)
    await pool.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_owner_conditions_identity_episode
        ON public.owner_conditions (source, fingerprint, episode)
    """)
