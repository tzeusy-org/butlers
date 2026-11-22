"""Deterministic liveness conditions for QA's own remediation loop (bu-ejow5p.8).

QA cannot investigate itself (RFC 0015 D3) and cannot read Beads, so the health of its
remediation loop is judged here, from the durable outcome rows QA already writes, by the
independent control-plane controller (``butlers.core.fleet_conditions.run_controller_pass``).

Four conditions, each with a fixed server-derived identity in ``public.infra_conditions``:

``capability_below_target``
    The most recent launched investigation ended on a structural cause (for example
    ``publication_unavailable`` = no publisher configured). One identity per cause.
``breaker_open_too_long``
    The QA circuit breaker has been tripped longer than :data:`BREAKER_OPEN_AFTER`.
``proposal_unadopted``
    A retained proposal has sat without a pull request for longer than
    :data:`PROPOSAL_UNADOPTED_AFTER`.
``no_verified_fix``
    Zero merged fixes over :data:`FUNNEL_WINDOW` despite at least :data:`FUNNEL_MIN_OPPORTUNITIES`
    investigations that reached the publication stage.

Every check is three-valued (RFC 0029): present, absent, or unmeasurable. An unreadable table or
an empty opportunity set is *unmeasurable*: it neither opens nor resolves a condition. Only a
complete, measurable observation of absence resolves one. Nothing here publishes, unpauses or
widens authority; the module only records conditions and refuses a cause-less breaker reset.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final, Literal

from butlers.core.healing.constants import CIRCUIT_BREAKER_FAILURE_STATUSES
from butlers.core.infra_conditions import (
    ConditionTransition,
    Observation,
    compute_fingerprint,
    reconcile_snapshot,
)
from butlers.core.job_health import mark_tick_incomplete

logger = logging.getLogger(__name__)

IDENTITY_VERSION: Final[int] = 1
BREAKER_THRESHOLD: Final[int] = 5
BREAKER_OPEN_AFTER: Final[timedelta] = timedelta(hours=6)
PROPOSAL_UNADOPTED_AFTER: Final[timedelta] = timedelta(days=7)
FUNNEL_WINDOW: Final[timedelta] = timedelta(days=14)
FUNNEL_MIN_OPPORTUNITIES: Final[int] = 3
_GRACE_S: Final[float] = 3600.0

CAPABILITY_SOURCE: Final[str] = "qa_loop_capability"
BREAKER_SOURCE: Final[str] = "qa_loop_breaker"
PROPOSAL_SOURCE: Final[str] = "qa_loop_proposal"
FUNNEL_SOURCE: Final[str] = "qa_loop_funnel"
LOOP_SOURCES: Final[tuple[str, ...]] = (
    CAPABILITY_SOURCE,
    BREAKER_SOURCE,
    PROPOSAL_SOURCE,
    FUNNEL_SOURCE,
)

#: ``healing_attempts.error_detail`` prefix -> cause code. Structural causes need a deploy or a
#: configuration change, never another attempt.
STRUCTURAL_CAUSE_MARKERS: Final[dict[str, str]] = {
    "publication_unavailable": "publisher_unconfigured",
    "publication_unretained": "proposal_retention_unavailable",
}
EXECUTION_FAILURE: Final[str] = "execution_failure"

#: Statuses that mean an investigation reached the publication stage.
_PUBLICATION_STAGE_STATUSES: Final[tuple[str, ...]] = (
    "pr_open",
    "pr_merged",
    "proposal_retained",
)

_LAUNCHED = """
    FROM public.healing_attempts
    WHERE qa_patrol_id IS NOT NULL
      AND closed_at IS NOT NULL
      AND healing_session_id IS NOT NULL
"""
_LATEST_ATTEMPT_SQL = (
    f"SELECT status, error_detail, closed_at {_LAUNCHED} ORDER BY closed_at DESC LIMIT 1"
)
#: Same filter as dispatch admission: only attempts closed after the latest reset count.
STREAK_SQL: Final[str] = f"""
    SELECT status, error_detail, closed_at {_LAUNCHED}
      AND closed_at > COALESCE(
            (SELECT max(reset_at) FROM public.breaker_resets WHERE breaker = 'qa'),
            '-infinity'::timestamptz)
    ORDER BY closed_at DESC
    LIMIT $1
"""
_NOW_SQL = "SELECT clock_timestamp()"
_UNADOPTED_SQL = """
    SELECT count(*) AS n, min(updated_at) AS oldest
    FROM public.healing_attempts
    WHERE qa_patrol_id IS NOT NULL
      AND status = 'proposal_retained'
      AND pr_url IS NULL
      AND updated_at < clock_timestamp() - make_interval(secs => $1)
"""
_FUNNEL_SQL = f"""
    SELECT count(*) FILTER (
               WHERE status = ANY($2::text[])
                  OR error_detail LIKE ANY($3::text[])) AS opportunities,
           count(*) FILTER (WHERE status = 'pr_merged') AS merged
    {_LAUNCHED}
      AND closed_at >= clock_timestamp() - make_interval(secs => $1)
"""

State = Literal["present", "absent", "unmeasurable"]


def classify_cause(error_detail: str | None) -> str:
    """Return the cause code for one closed attempt's ``error_detail``."""
    for marker, cause in STRUCTURAL_CAUSE_MARKERS.items():
        if error_detail and error_detail.startswith(marker):
            return cause
    return EXECUTION_FAILURE


def is_structural(cause: str) -> bool:
    return cause in STRUCTURAL_CAUSE_MARKERS.values()


def dominant_cause(error_details: Sequence[str | None]) -> str | None:
    """Most frequent cause in a streak (newest first); ties go to the newest.

    ``None`` for an empty streak: there is no evidence to name a cause.
    """
    if not error_details:
        return None
    causes = [classify_cause(detail) for detail in error_details]
    counts = Counter(causes)
    best = max(counts.values())
    return next(cause for cause in causes if counts[cause] == best)


def fingerprint(source: str, **facts: str) -> str:
    return compute_fingerprint(source, IDENTITY_VERSION, {"source": source, **facts})


def capability_fingerprint(cause: str) -> str:
    return fingerprint(CAPABILITY_SOURCE, condition="capability_below_target", cause=cause)


BREAKER_FINGERPRINT: Final[str] = fingerprint(BREAKER_SOURCE, condition="breaker_open_too_long")
PROPOSAL_FINGERPRINT: Final[str] = fingerprint(PROPOSAL_SOURCE, condition="proposal_unadopted")
FUNNEL_FINGERPRINT: Final[str] = fingerprint(FUNNEL_SOURCE, condition="no_verified_fix")


@dataclass(frozen=True)
class Measurement:
    """One condition check: its three-valued state and, when present, its observation."""

    source: str
    state: State
    observation: Observation | None = None


async def _safe(pool: Any, call: str, *args: Any) -> Any | None:
    """Run one read; an unreadable table is unmeasurable, never agreement."""
    try:
        if call == "fetch":
            return list(await pool.fetch(*args))
        if call == "fetchrow":
            return await pool.fetchrow(*args)
        return await pool.fetchval(*args)
    except Exception as exc:  # noqa: BLE001 - reduced to a typed log
        mark_tick_incomplete()
        logger.warning("QA remediation liveness: read failed (category=%s)", type(exc).__name__)
        return None


async def measure_capability(pool: Any) -> Measurement:
    row = await _safe(pool, "fetchrow", _LATEST_ATTEMPT_SQL)
    if row is None:
        return Measurement(CAPABILITY_SOURCE, "unmeasurable")
    cause = classify_cause(row["error_detail"])
    if not is_structural(cause):
        return Measurement(CAPABILITY_SOURCE, "absent")
    return Measurement(
        CAPABILITY_SOURCE,
        "present",
        Observation(
            fingerprint=capability_fingerprint(cause),
            summary=f"QA remediation capability is below target: {cause}"[:500],
            metadata={"cause": cause, "last_attempt_closed_at": row["closed_at"].isoformat()},
            identity_version=IDENTITY_VERSION,
        ),
    )


async def measure_breaker_open(pool: Any) -> Measurement:
    rows = await _safe(pool, "fetch", STREAK_SQL, BREAKER_THRESHOLD)
    now = await _safe(pool, "fetchval", _NOW_SQL)
    if rows is None or now is None:
        return Measurement(BREAKER_SOURCE, "unmeasurable")
    tripped = len(rows) >= BREAKER_THRESHOLD and all(
        row["status"] in CIRCUIT_BREAKER_FAILURE_STATUSES for row in rows
    )
    if not tripped:
        return Measurement(BREAKER_SOURCE, "absent")
    open_for = now - rows[0]["closed_at"]
    if open_for <= BREAKER_OPEN_AFTER:
        return Measurement(BREAKER_SOURCE, "absent")
    cause = dominant_cause([row["error_detail"] for row in rows])
    return Measurement(
        BREAKER_SOURCE,
        "present",
        Observation(
            fingerprint=BREAKER_FINGERPRINT,
            summary=f"QA circuit breaker open for {int(open_for.total_seconds() // 3600)}h"[:500],
            metadata={"dominant_cause": cause, "tripped_at": rows[0]["closed_at"].isoformat()},
            identity_version=IDENTITY_VERSION,
        ),
    )


async def measure_proposal_unadopted(pool: Any) -> Measurement:
    row = await _safe(pool, "fetchrow", _UNADOPTED_SQL, PROPOSAL_UNADOPTED_AFTER.total_seconds())
    if row is None:
        return Measurement(PROPOSAL_SOURCE, "unmeasurable")
    if not row["n"]:
        return Measurement(PROPOSAL_SOURCE, "absent")
    return Measurement(
        PROPOSAL_SOURCE,
        "present",
        Observation(
            fingerprint=PROPOSAL_FINGERPRINT,
            summary=(
                f"{int(row['n'])} retained proposal(s) unadopted for over "
                f"{PROPOSAL_UNADOPTED_AFTER.days} days"
            ),
            metadata={"count": int(row["n"]), "oldest_at": row["oldest"].isoformat()},
            identity_version=IDENTITY_VERSION,
        ),
    )


async def measure_funnel(pool: Any) -> Measurement:
    """Absence claim: made only when at least one opportunity exists to measure it against."""
    markers = [f"{marker}%" for marker in STRUCTURAL_CAUSE_MARKERS]
    row = await _safe(
        pool,
        "fetchrow",
        _FUNNEL_SQL,
        FUNNEL_WINDOW.total_seconds(),
        list(_PUBLICATION_STAGE_STATUSES),
        markers,
    )
    if row is None or not row["opportunities"]:
        return Measurement(FUNNEL_SOURCE, "unmeasurable")
    opportunities, merged = int(row["opportunities"]), int(row["merged"])
    if merged > 0 or opportunities < FUNNEL_MIN_OPPORTUNITIES:
        return Measurement(FUNNEL_SOURCE, "absent")
    return Measurement(
        FUNNEL_SOURCE,
        "present",
        Observation(
            fingerprint=FUNNEL_FINGERPRINT,
            summary=(
                f"No merged QA fix in {FUNNEL_WINDOW.days} days despite "
                f"{opportunities} investigations that reached publication"
            ),
            metadata={
                "opportunities": opportunities,
                "merged": merged,
                "window_days": FUNNEL_WINDOW.days,
            },
            identity_version=IDENTITY_VERSION,
        ),
    )


async def reconcile_remediation_loop(pool: Any) -> list[ConditionTransition]:
    """Measure the four conditions and reconcile each source; no check blocks another."""
    transitions: list[ConditionTransition] = []
    for measure in (
        measure_capability,
        measure_breaker_open,
        measure_proposal_unadopted,
        measure_funnel,
    ):
        measurement = await measure(pool)
        if measurement.state == "unmeasurable":
            continue  # Neither opens nor resolves on unknown evidence.
        observations = [measurement.observation] if measurement.observation is not None else []
        try:
            transitions.extend(
                await reconcile_snapshot(
                    pool,
                    source=measurement.source,
                    observations=observations,
                    snapshot_complete=True,
                    initial_grace_seconds=_GRACE_S,
                )
            )
        except Exception:
            mark_tick_incomplete()
            logger.exception("QA remediation liveness: reconcile failed (%s)", measurement.source)
    return transitions


_ACTIVE_SQL = """
    SELECT source, summary, metadata, first_detected_at
    FROM public.infra_conditions
    WHERE source = ANY($1::text[]) AND state IN ('open', 'aging')
    ORDER BY first_detected_at
"""


async def active_loop_conditions(pool: Any) -> list[dict[str, Any]] | None:
    """Active loop conditions for the summary; ``None`` when they cannot be read."""
    rows = await _safe(pool, "fetch", _ACTIVE_SQL, list(LOOP_SOURCES))
    if rows is None:
        return None
    conditions = []
    for row in rows:
        metadata = row["metadata"] or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        conditions.append(
            {
                "source": row["source"],
                "summary": row["summary"],
                "cause": metadata.get("cause") or metadata.get("dominant_cause"),
                "first_detected_at": row["first_detected_at"],
            }
        )
    return conditions


@dataclass(frozen=True)
class ResetDecision:
    allowed: bool
    cause_code: str | None = None
    cause_ref: str | None = None
    reason: str | None = None


_MAX_REF = 200
_PRIOR_STRUCTURAL_SQL = """
    SELECT 1
    FROM public.breaker_resets r
    WHERE r.breaker = 'qa' AND r.cause_code = $1 AND r.cause_ref = $2
      AND EXISTS (
        SELECT 1 FROM public.healing_attempts a
        WHERE a.qa_patrol_id IS NOT NULL AND a.closed_at > r.reset_at
          AND a.error_detail LIKE $3)
    LIMIT 1
"""


def parse_cause_ref(raw: str | None) -> tuple[str, str] | None:
    """Split ``<cause_code>:<reference>``; both parts must be non-empty."""
    if raw is None:
        return None
    code, sep, ref = raw.strip().partition(":")
    code, ref = code.strip(), ref.strip()
    if not sep or not code or not ref or len(raw) > _MAX_REF:
        return None
    return code, ref


async def evaluate_reset(
    pool: Any, raw_cause_ref: str | None, details: Sequence[str | None]
) -> ResetDecision:
    """Decide whether a manual reset names a cause it can credibly claim to address.

    A reset must name ``<cause_code>:<reference>``. When the streak's dominant cause is
    structural, the code must match it, and a reference that already bought a reset whose
    attempts then failed on the same structural marker is refused: the claim was disproved.
    """
    parsed = parse_cause_ref(raw_cause_ref)
    if parsed is None:
        return ResetDecision(False, reason="cause_ref_required")
    code, ref = parsed
    dominant = dominant_cause(details)
    if dominant is not None and is_structural(dominant):
        if code != dominant:
            return ResetDecision(False, code, ref, reason=f"structural_cause_present:{dominant}")
        marker = next(m for m, c in STRUCTURAL_CAUSE_MARKERS.items() if c == dominant)
        try:
            prior = await pool.fetchval(_PRIOR_STRUCTURAL_SQL, code, ref, f"{marker}%")
        except Exception as exc:  # noqa: BLE001 - unreadable evidence refuses, never allows
            logger.warning(
                "QA breaker reset: prior-reset evidence unreadable (category=%s)",
                type(exc).__name__,
            )
            return ResetDecision(False, code, ref, reason="evidence_unreadable")
        if prior:
            return ResetDecision(False, code, ref, reason=f"structural_cause_unchanged:{dominant}")
    return ResetDecision(True, code, ref)
