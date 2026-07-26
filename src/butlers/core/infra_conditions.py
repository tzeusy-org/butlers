"""The infrastructure condition ledger — durable append-per-episode reliability evidence.

bu-27dxl.6.2 — implements the representation and transition semantics defined
by the merged ``define-infrastructure-reliability-lifecycle`` OpenSpec change
(bu-27dxl.6.1, PR #3522). See
``openspec/changes/define-infrastructure-reliability-lifecycle/design.md``
and its ``specs/infrastructure-reliability/spec.md`` for the full normative
contract this module implements: canonical condition identity (Decision #1),
append-per-episode snapshot-authoritative recovery (Decision #2), and bounded
lifecycle escalation (Decision #3).

bu-ep4ks.6 — the reconciliation engine (fingerprinting, open/confirm/escalate/
resolve, the advisory-lock concurrency contract, reads) moved to
``butlers.core.condition_ledger`` so it can back more than infrastructure
reliability: ``butlers.core.owner_conditions`` (bu-ep4ks.6) reuses the exact
same lifecycle machinery for owner-facing standing concerns (an overdue
bill, a refill due, an expiring document) that this module's docstring
previously scoped out. This module is now a thin facade binding
``table="public.infra_conditions"`` — every function signature, return type,
and behavior below is unchanged from before the extraction; existing callers
(``butlers.jobs.deploy_drift``, ``butlers.jobs.calendar_sync_deadman``) need
no changes. See ``condition_ledger.py`` for the full engine documentation
(design, concurrency contract).

This module has no producer of its own (``calendar_sync_deadman.py``,
``deploy_drift.py``) — later children (bu-27dxl.6.3+) wire those up against
``reconcile_snapshot`` below.

Identity-version-bump totality (bu-27dxl.6.2 review-input; deeper fix tracked
as bu-rxo0l)
---------------------------------------------------------------------------
Decision #1 lets a producer bump its identity-payload version without
reinterpreting a prior episode's identity — see
:func:`butlers.core.condition_ledger.compute_fingerprint`. A version bump
computes a *new* fingerprint; it never rewrites an existing episode's stored
``fingerprint``. Read naively, that would leave an episode open/aging under a
retired fingerprint permanently un-exitable, since no future observation
will ever carry that fingerprint again (enterable, not exitable).

This module closes that gap structurally rather than adding a new terminal
state or a separate "superseded" status. ``reconcile_snapshot``'s
complete-snapshot resolution scope is ``source`` alone, never
``(source, fingerprint)`` or an identity version: a complete snapshot
enumerates everything the producer currently observes under its CURRENT
identity scheme, and ANY active episode for that source absent from that
enumeration is resolved — including one still keyed by a fingerprint the
producer stopped emitting after a version bump. This is not a special case;
it is the same "absent from a complete snapshot proves recovery" rule
Decision #2 already defines for ordinary recovery. A version bump makes the
old fingerprint permanently absent from every future snapshot, and permanent
absence from a complete snapshot is precisely what that rule already
resolves — see :func:`~butlers.core.condition_ledger._reconcile_source_locked`'s
absence pass, which is keyed by ``source`` and never re-filters by
fingerprint version.

When the first observation under the newer contract explicitly names the
retired fingerprint as its predecessor, the shared ledger persists reciprocal
episode links and records ``superseded_by_identity_version_bump`` rather than
presenting the absence as recovery. The old fingerprint remains immutable;
unlinked version changes and incomplete snapshots retain the ordinary
snapshot-absence behavior.
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

__all__ = [
    "ESCALATION_LEVELS",
    "VALID_STATES",
    "ConditionTransition",
    "Observation",
    "TransitionKind",
    "compute_fingerprint",
    "get_active_condition",
    "list_conditions",
    "reconcile_snapshot",
]

_TABLE = "public.infra_conditions"


async def reconcile_snapshot(
    pool: asyncpg.Pool,
    *,
    source: str,
    observations: Sequence[Observation],
    snapshot_complete: bool,
    initial_grace_seconds: float,
) -> list[ConditionTransition]:
    """Atomically reconcile one producer check-in against the infra condition ledger.

    See ``butlers.core.condition_ledger.reconcile_snapshot`` for the full
    contract (AC1-AC4). This facade binds ``table="public.infra_conditions"``.
    """
    return await _reconcile_snapshot(
        pool,
        table=_TABLE,
        source=source,
        observations=observations,
        snapshot_complete=snapshot_complete,
        initial_grace_seconds=initial_grace_seconds,
    )


async def get_active_condition(
    pool: asyncpg.Pool, *, source: str, fingerprint: str
) -> dict[str, Any] | None:
    """Return the active (``open``/``aging``) episode for ``(source, fingerprint)``.

    Returns ``None`` when there is no active episode — either the identity
    has never been observed, or its most recent episode already resolved.
    Intended for future producer/QA-suppression consumers (bu-27dxl.6.3+)
    that need "is this condition currently active" without reconciling.
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
