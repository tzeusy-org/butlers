"""Independent fleet and QA-patrol conditions (REQ-butler-control-plane-liveness-005).

The Dashboard's supervised receiver observer calls :func:`run_controller_pass`
after each cycle. It depends on neither QA's scheduler nor a routable QA
registry row, so it still reports when QA itself is down. It writes two
sources in ``public.infra_conditions``:

``control_plane_fleet``
    One versioned common-cause condition for every expected daemon a cycle
    could not prove receiver-ready. Per-daemon impact is evidence, not
    identity, so thirteen stale daemons from one observer fault are one
    episode. Only a complete snapshot with no affected daemon resolves it; an
    incomplete cycle adds evidence and never resolves. A daemon whose owner
    policy is ``paused`` is an intentional exclusion.

``qa_patrol_assurance``
    ``patrol_overdue`` when no qualifying QA patrol (see
    ``butlers.core.qa.patrol_provenance``) completed within twice its
    configured cadence. When QA is held by owner policy the same absence is
    recorded as the distinct ``patrol_stopped_by_policy`` identity.

Owner attention (REQ-butler-control-plane-liveness-007): every pass also asks
the fixed ``public.append_runtime_attention_condition`` producer, under
Switchboard's role, for one outbox episode per active fleet or overdue-QA
condition episode. The producer owns the attention grace and idempotency, so
repeating the call each cycle is how an interrupted append is repaired; a
condition that already paged returns the same episode and never re-pages.
The stopped-by-policy identity is an intentional hold and is not paged.

Fleet-condition handoff (``BUTLERS_FLEET_CONDITION_HANDOFF=1``): QA's
``infra_state`` source stops emitting per-butler ``ButlerHeartbeatStale``
findings and records one fleet-linked finding instead. Pre-existing per-butler
episodes stay open and linked here; this controller resolves each only after a
complete snapshot observes that daemon healthy. Cutover alone is not recovery.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Final

from butlers.core.control_plane_identity import ShadowCycle
from butlers.core.infra_conditions import (
    ConditionTransition,
    Observation,
    compute_fingerprint,
    reconcile_snapshot,
    resolve_condition,
)
from butlers.core.qa.patrol_provenance import (
    LAST_QUALIFYING_PATROL_SQL,
    QUALIFYING_PATROL_STATUSES,
    QaPatrolContract,
)

logger = logging.getLogger(__name__)

FLEET_SOURCE: Final[str] = "control_plane_fleet"
QA_PATROL_SOURCE: Final[str] = "qa_patrol_assurance"
IDENTITY_VERSION: Final[int] = 1
FLEET_FINGERPRINT: Final[str] = compute_fingerprint(
    FLEET_SOURCE, IDENTITY_VERSION, {"condition": "receiver_fleet_unready"}
)
QA_PATROL_OVERDUE_FINGERPRINT: Final[str] = compute_fingerprint(
    QA_PATROL_SOURCE, IDENTITY_VERSION, {"condition": "patrol_overdue"}
)
QA_PATROL_STOPPED_FINGERPRINT: Final[str] = compute_fingerprint(
    QA_PATROL_SOURCE, IDENTITY_VERSION, {"condition": "patrol_stopped_by_policy"}
)

#: ``infra_state``'s per-butler liveness finding type, the legacy predecessor.
LEGACY_LIVENESS_EXCEPTION_TYPE: Final[str] = "ButlerHeartbeatStale"
_LEGACY_SOURCE: Final[str] = "infra_state"
_HANDOFF_ENV: Final[str] = "BUTLERS_FLEET_CONDITION_HANDOFF"
_CONDITION_INITIAL_GRACE_S: Final[float] = 3600.0
_MAX_AFFECTED_EVIDENCE: Final[int] = 100

_ACTIVE_ATTENTION_CONDITIONS_SQL: Final[str] = """
    SELECT id FROM public.infra_conditions
    WHERE state IN ('open', 'aging')
      AND ((source = $1 AND fingerprint = $2) OR (source = $3 AND fingerprint = $4))
    ORDER BY first_detected_at
"""
_APPEND_ATTENTION_SQL: Final[str] = "SELECT public.append_runtime_attention_condition($1)"

_ACTIVE_LEGACY_LIVENESS_SQL: Final[str] = """
    SELECT id, fingerprint, summary, metadata, metadata->>'source_butler' AS butler
    FROM public.infra_conditions
    WHERE source = $1 AND state IN ('open', 'aging')
      AND metadata->>'exception_type' = $2
    ORDER BY first_detected_at
"""


def fleet_condition_handoff_enabled() -> bool:
    return os.environ.get(_HANDOFF_ENV) == "1"


async def active_legacy_liveness_conditions(pool: Any) -> list[Any]:
    """Return open per-butler liveness episodes the fleet condition supersedes."""
    return list(
        await pool.fetch(
            _ACTIVE_LEGACY_LIVENESS_SQL, _LEGACY_SOURCE, LEGACY_LIVENESS_EXCEPTION_TYPE
        )
    )


async def reconcile_fleet_condition(
    pool: Any, cycle: ShadowCycle, expected_names: frozenset[str]
) -> list[ConditionTransition]:
    """Record one fleet condition from a receiver cycle; resolve only on complete health.

    A legacy per-butler episode resolves only when its daemon is in the exact
    expected roster and this complete cycle proved it receiver-ready.
    """
    affected = sorted(
        (d for d in cycle.unready if d.policy_state != "paused"), key=lambda d: d.name
    )
    unready_names = {d.name for d in cycle.unready}
    legacy = await active_legacy_liveness_conditions(pool)

    if cycle.complete and fleet_condition_handoff_enabled():
        for row in legacy:
            if row["butler"] in expected_names and row["butler"] not in unready_names:
                await resolve_condition(
                    pool,
                    source=_LEGACY_SOURCE,
                    fingerprint=row["fingerprint"],
                    resolution_metadata={
                        "resolution_reason": "complete_receiver_snapshot_healthy",
                        "fleet_condition_fingerprint": FLEET_FINGERPRINT,
                    },
                )

    if not affected and not cycle.complete:
        return []  # Nothing observed; an incomplete cycle proves no recovery.
    observations = []
    if affected:
        names = [d.name for d in affected]
        observations.append(
            Observation(
                fingerprint=FLEET_FINGERPRINT,
                summary=(
                    f"{len(affected)} of {cycle.expected_count} expected daemons are not "
                    f"receiver-ready: {', '.join(names)}"
                )[:500],
                metadata={
                    "affected": [
                        {"name": d.name, "category": d.category, "policy_state": d.policy_state}
                        for d in affected[:_MAX_AFFECTED_EVIDENCE]
                    ],
                    "affected_count": len(affected),
                    "expected_count": cycle.expected_count,
                    "snapshot_complete": cycle.complete,
                    "linked_legacy_conditions": [
                        {"condition_id": str(row["id"]), "butler": row["butler"]}
                        for row in legacy[:_MAX_AFFECTED_EVIDENCE]
                    ],
                },
                identity_version=IDENTITY_VERSION,
            )
        )
    return await reconcile_snapshot(
        pool,
        source=FLEET_SOURCE,
        observations=observations,
        snapshot_complete=cycle.complete,
        initial_grace_seconds=_CONDITION_INITIAL_GRACE_S,
    )


def _policy_stops_qa(policy: Any) -> bool:
    if policy is None:
        return False
    state, provenance = policy["policy_state"], policy["policy_provenance"]
    return state in ("paused", "quarantined") or (
        state == "review_required" and provenance == "operator"
    )


async def reconcile_qa_patrol_assurance(
    pool: Any, policy_reader: Any, contract: QaPatrolContract
) -> list[ConditionTransition]:
    """Check qualifying QA patrol age against twice its cadence, independently of QA."""
    last: datetime | None = await pool.fetchval(
        LAST_QUALIFYING_PATROL_SQL, list(QUALIFYING_PATROL_STATUSES), contract.digest
    )
    now: datetime = await pool.fetchval("SELECT clock_timestamp()")
    if last is not None and (now - last).total_seconds() <= contract.overdue_after_s:
        return await reconcile_snapshot(
            pool,
            source=QA_PATROL_SOURCE,
            observations=[],
            snapshot_complete=True,
            initial_grace_seconds=_CONDITION_INITIAL_GRACE_S,
        )

    policy_known = True
    try:
        policy = await policy_reader.fetchrow(
            "SELECT policy_state, policy_provenance "
            "FROM switchboard.butler_registry_control_plane WHERE name = 'qa'"
        )
    except Exception:
        logger.warning("QA patrol assurance: QA policy unreadable (category=policy_unavailable)")
        policy, policy_known = None, False
    stopped = _policy_stops_qa(policy)
    age = "never" if last is None else f"{int((now - last).total_seconds() // 60)}m"
    observation = Observation(
        fingerprint=QA_PATROL_STOPPED_FINGERPRINT if stopped else QA_PATROL_OVERDUE_FINGERPRINT,
        summary=(
            f"No qualifying QA patrol for {age} (limit "
            f"{int(contract.overdue_after_s // 60)}m)"
            + ("; QA is held by owner policy" if stopped else "")
        ),
        metadata={
            "last_qualifying_completed_at": last.isoformat() if last is not None else None,
            "overdue_after_s": contract.overdue_after_s,
            "enabled_sources_config_digest": contract.digest,
            "policy_state": policy["policy_state"] if policy is not None else None,
            "policy_known": policy_known,
        },
        identity_version=IDENTITY_VERSION,
    )
    # Without a readable policy the overdue/stopped split is unproven, so the
    # observation adds evidence without resolving the other identity.
    return await reconcile_snapshot(
        pool,
        source=QA_PATROL_SOURCE,
        observations=[observation],
        snapshot_complete=policy_known,
        initial_grace_seconds=_CONDITION_INITIAL_GRACE_S,
    )


async def append_due_condition_attention(pool: Any, producer: Any) -> list[Any]:
    """Append or repair one attention episode per active paging condition.

    ``producer`` must run under Switchboard's role; the database refuses any
    other. Returns the episode ids the producer reported (``None`` while a
    condition is still inside its attention grace or the producer is off).
    A failing append for one condition never blocks the next.
    """
    rows = await pool.fetch(
        _ACTIVE_ATTENTION_CONDITIONS_SQL,
        FLEET_SOURCE,
        FLEET_FINGERPRINT,
        QA_PATROL_SOURCE,
        QA_PATROL_OVERDUE_FINGERPRINT,
    )
    episodes: list[Any] = []
    for row in rows:
        try:
            episodes.append(await producer.fetchval(_APPEND_ATTENTION_SQL, row["id"]))
        except Exception as exc:  # noqa: BLE001 - reduced to a typed log
            logger.warning(
                "Fleet condition controller: attention append failed (category=%s)",
                type(exc).__name__,
            )
    return episodes


async def run_controller_pass(
    pool: Any,
    policy_reader: Any,
    cycle: ShadowCycle,
    expected_names: frozenset[str],
    qa_contract: QaPatrolContract | None,
) -> None:
    """Run both independent checks, then attention; no step blocks another."""
    try:
        await reconcile_fleet_condition(pool, cycle, expected_names)
    except Exception:
        logger.exception("Fleet condition controller: fleet reconciliation failed")
    if qa_contract is not None:
        try:
            await reconcile_qa_patrol_assurance(pool, policy_reader, qa_contract)
        except Exception:
            logger.exception("Fleet condition controller: QA patrol assurance failed")
    try:
        await append_due_condition_attention(pool, policy_reader)
    except Exception:
        logger.exception("Fleet condition controller: attention append failed")


def controller_after_cycle(
    pool: Any, configs: list[Any]
) -> Callable[[Any, ShadowCycle], Awaitable[None]]:
    """Bind the controller to the exact Git roster for the observer's ``after_cycle``.

    ``pool`` writes the condition ledger and reads ``public.qa_patrols``; the
    observer's Switchboard role view reads QA's owner policy and is the only
    identity the attention producer accepts.
    """
    expected_names = frozenset(config.name for config in configs)
    qa_contract = next(
        (getattr(config, "qa_patrol_contract", None) for config in configs if config.name == "qa"),
        None,
    )

    async def after_cycle(policy_reader: Any, cycle: ShadowCycle) -> None:
        await run_controller_pass(pool, policy_reader, cycle, expected_names, qa_contract)

    return after_cycle
