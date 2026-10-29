"""Dropped-known watch job — one owner condition per known-contact drop episode.

bu-q7vx1q.88.2 (design: ``openspec/changes/surface-known-contact-drops``). Scheduled via
``butler.toml`` (``dispatch_mode = "job"``, ``job_name = "dropped_known_watch"``) and loaded
dynamically by ``src/butlers/scheduled_jobs.py`` the same way as ``rule_promotion_trigger.py``.

Each tick reads the unanswered marked drops from known contacts in the trailing window, groups
them by ``(filter_reason, sender_identity)`` and reconciles that complete set into the owner
condition ledger under source ``switchboard:dropped-known``:

* a new episode opens a standing condition and proposes ONE insight candidate (an ``opened`` or
  ``reopened`` transition only; a later scan that only updates the count proposes nothing);
* an episode whose rows were replayed, or aged out of the window, is absent from the snapshot and
  resolves;
* when current or historical classification is unknown, nothing is reconciled at all, because an
  unknown read must never resolve an episode.

The condition identity is a hash of the pair, and neither the snapshot nor the candidate carries
the sender address, a subject, a body or the filter reason.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.core.condition_ledger import Observation, compute_fingerprint
from butlers.core.ingestion_events import (
    ingestion_classification_uncertainty,
    ingestion_current_classification_available,
    ingestion_dropped_known_episodes,
)
from butlers.core.insight_premise import owner_condition_premise
from butlers.core.owner_conditions import reconcile_snapshot
from butlers.tools.switchboard.insight.broker import propose_insight_candidate

logger = logging.getLogger(__name__)

SOURCE = "switchboard:dropped-known"
IDENTITY_VERSION = 1
WINDOW = timedelta(days=7)
#: The owner is told on the opened transition regardless of grace; this only sets when the
#: ledger's L1 escalation becomes due, matching the broker default and the other producers.
_GRACE_S = 3600.0
_CATEGORY = "dropped-known"
_PRIORITY = 40
_MESSAGE = "Mail from someone you know was dropped by a filter rule. Review it in Filters."


def _fingerprint(filter_reason: str | None, sender_identity: str | None) -> str:
    return compute_fingerprint(
        SOURCE,
        IDENTITY_VERSION,
        {"filter_reason": filter_reason, "sender_identity": sender_identity},
    )


async def run_dropped_known_watch_job(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reconcile known-contact drop episodes into the owner condition ledger.

    Takes no ``job_args``. Returns ``{"status": "ok", "episodes", "opened", "reopened",
    "resolved", "proposed"}``, or ``{"status": "degraded", "reason": ...}`` when the read or the
    classification is unknown (no state change).
    """
    if job_args:
        raise ValueError(f"dropped_known_watch job takes no job_args; got: {sorted(job_args)}")
    now = datetime.now(UTC)
    from_dt = now - WINDOW
    try:
        classification = await ingestion_current_classification_available(db_pool, now=now)
        uncertain = await ingestion_classification_uncertainty(db_pool, from_dt=from_dt, to_dt=now)
        episodes = await ingestion_dropped_known_episodes(db_pool, from_dt=from_dt, to_dt=now)
    except Exception:
        logger.warning("Dropped-known watch: read failed; no state change", exc_info=True)
        return {"status": "degraded", "reason": "read_failed"}
    if not classification or uncertain:
        reason = "classification_unknown" if not classification else "historical_uncertainty"
        logger.warning("Dropped-known watch: %s; no state change", reason)
        return {"status": "degraded", "reason": reason}

    observations = [
        Observation(
            fingerprint=_fingerprint(row["filter_reason"], row["sender_identity"]),
            identity_version=IDENTITY_VERSION,
            summary=f"{row['count']} message(s) from a known contact dropped by a filter rule",
            metadata={
                "count": row["count"],
                "first_received_at": row["first_received_at"].isoformat(),
                "last_received_at": row["last_received_at"].isoformat(),
                "window_days": WINDOW.days,
            },
        )
        for row in episodes
    ]
    transitions = await reconcile_snapshot(
        db_pool,
        source=SOURCE,
        observations=observations,
        snapshot_complete=True,
        initial_grace_seconds=_GRACE_S,
    )

    proposed = 0
    for transition in transitions:
        if transition.transition not in ("opened", "reopened"):
            continue
        result = await propose_insight_candidate(
            db_pool,
            origin_butler="switchboard",
            priority=_PRIORITY,
            category=_CATEGORY,
            dedup_key=f"{_CATEGORY}:{transition.condition_id}:{transition.episode}",
            message=_MESSAGE,
            expires_at=now + WINDOW,
            metadata={"door": "filters", "condition_id": str(transition.condition_id)},
            premise=owner_condition_premise(SOURCE, transition.fingerprint),
            now=now,
        )
        if result.get("status") == "accepted":
            proposed += 1
        else:
            logger.info("Dropped-known watch: candidate not queued: %s", result.get("reason"))

    summary = {
        "status": "ok",
        "episodes": len(episodes),
        "opened": sum(t.transition == "opened" for t in transitions),
        "reopened": sum(t.transition == "reopened" for t in transitions),
        "resolved": sum(t.transition == "resolved" for t in transitions),
        "proposed": proposed,
    }
    logger.info("Dropped-known watch job completed: %s", summary)
    return summary
