"""Insight outcome ledger: did a premised nudge change anything? (bu-s11n0s.15 S1).

Observe-only. Nothing here changes what the broker sends; slice 2 rolls the ledger up into
per-class verdicts and slice 3 may let a measured verdict shape delivery behind an owner opt-in.

Two deterministic steps, run daily by the ``insight_outcome_sweep`` job before the morning
delivery window:

- :func:`enroll_terminal_candidates` snapshots every terminal premise-bound candidate whose
  observation window is still open into ``public.insight_outcomes`` with its *exposure*: what
  actually reached the owner. ``delivered``/``coalesced`` are the treated arm; ``held_budget``
  and ``held_context`` are natural holdouts (nothing is withheld deliberately). Everything else is
  excluded from both arms: ``withdrawn`` (the premise was already false at send),
  ``shadowed`` (a dedup/cooldown sibling carried it), ``held_custody`` (delivery refused because
  the owner endpoint was under a custody hold, a lost-device window, not an attention choice),
  ``held_unverified`` (its premise could not be checked) and ``failed`` (the transport gave up).
- :func:`sweep_outcomes` records, once, whether each open row's premise resolved, when, and how
  precisely that is known. An ``owner_condition`` premise reads its condition episode exactly
  (``resolved_at`` and ``metadata.resolution_reason``, ``unspecified`` when absent). A ``probe``
  premise asks :func:`check_premise`; a probe can only say the fact stopped holding *by* the
  sweep, so its resolution is ``observed_by``. Three unknown answers settle ``unmeasurable``, never
  "ineffective". A premise still holding when the window closes settles ``persisted``.

Every write is guarded on ``outcome IS NULL``, enrollment is ``ON CONFLICT DO NOTHING`` and the
sweep holds a session advisory lock, so a rerun or an overlapping run changes nothing.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.core.attention_ledger import URGENT_PRIORITY_THRESHOLD
from butlers.core.insight_premise import PREMISE_KIND_OWNER_CONDITION, PREMISE_KIND_PROBE

from .broker import dedup_family
from .premises import check_premise

logger = logging.getLogger(__name__)

#: How long after a candidate was proposed its premise is watched.
OBSERVATION_WINDOW = timedelta(days=14)
#: Unknown premise answers before a row settles ``unmeasurable``.
MAX_UNKNOWN_CHECKS = 3
#: Rows handled per enroll or sweep run; the next daily run continues.
BATCH_LIMIT = 500
_SWEEP_LOCK = 353_15_0001

TREATED_EXPOSURES = frozenset({"delivered", "coalesced"})
HOLDOUT_EXPOSURES = frozenset({"held_budget", "held_context"})
EXCLUDED_EXPOSURES = frozenset(
    {"withdrawn", "shadowed", "held_custody", "held_unverified", "failed"}
)
EXPOSURES = TREATED_EXPOSURES | HOLDOUT_EXPOSURES | EXCLUDED_EXPOSURES

_TERMINAL = ("delivered", "expired", "filtered", "withdrawn")
_SHADOWING = frozenset({"dedup", "cooldown"})
#: The refusal reason CustodyEgressRefused names in its message, which the broker's failed
#: ledger row carries; ``_`` is a LIKE wildcard, so the pattern escapes it.
_CUSTODY_HELD = "endpoint_held"
_CUSTODY_HELD_LIKE = "%" + _CUSTODY_HELD.replace("_", "\\_") + "%"


def _obj(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, Mapping) else {}


def classify_exposure(
    status: str,
    metadata: Mapping[str, Any] | None,
    delivery_ref: Mapping[str, Any] | None,
    *,
    custody_refused: bool = False,
) -> str:
    """Name what reached the owner for one terminal candidate.

    A delivery refused by an endpoint custody hold is recognised from the candidate's own
    attention-ledger failures (``custody_refused``); it wins over the ledger reason the candidate
    finally settled with, because a lost-device window says nothing about the owner's attention.
    """
    metadata = metadata or {}
    if status == "delivered":
        if (delivery_ref or {}).get("digest_line_index") is not None:
            return "coalesced"
        return "delivered"
    if status == "withdrawn":
        return "withdrawn"
    if custody_refused:
        return "held_custody"
    blocked_by = metadata.get("blocked_by")
    if status == "filtered":
        if metadata.get("delivery_failure"):
            return "failed"
        if blocked_by in _SHADOWING:
            return "shadowed"
        # Verbosity off filters without restamping the reason: the owner's context held it.
        return "held_context"
    # expired: the last reason the delivery cycle held it for, budget when it never said.
    if blocked_by in _SHADOWING:
        return "shadowed"
    if blocked_by == "held_by":
        return "held_context"
    if blocked_by == "premise_unknown":
        return "held_unverified"
    return "held_budget"


async def enroll_terminal_candidates(
    pool: asyncpg.Pool, *, now: datetime | None = None, limit: int = BATCH_LIMIT
) -> int:
    """Snapshot terminal premised candidates whose window is still open. Returns rows added."""
    if now is None:
        now = datetime.now(UTC)
    rows = await pool.fetch(
        """
        SELECT c.id, c.origin_butler, c.category, c.dedup_key, c.priority,
               c.premise::text AS premise, c.status, c.metadata::text AS metadata,
               c.delivery_ref::text AS delivery_ref,
               c.created_at, c.delivered_at
        FROM public.insight_candidates c
        WHERE c.premise IS NOT NULL
          AND c.status = ANY($1::text[])
          AND c.created_at > $2
          AND NOT EXISTS (SELECT 1 FROM public.insight_outcomes o WHERE o.candidate_id = c.id)
        ORDER BY c.created_at
        LIMIT $3
        """,
        list(_TERMINAL),
        now - OBSERVATION_WINDOW,
        limit,
    )
    if not rows:
        return 0
    ids = [row["id"] for row in rows]
    refused = {
        str(row["notification_ref"])
        for row in await pool.fetch(
            """
            SELECT DISTINCT notification_ref FROM public.attention_ledger
            WHERE source = 'insight' AND outcome = 'failed'
              AND notification_ref = ANY($1::text[])
              AND reason LIKE $2
            """,
            [str(i) for i in ids],
            _CUSTODY_HELD_LIKE,
        )
    }
    added = 0
    for row in rows:
        premise = _obj(row["premise"])
        if not premise:
            continue
        exposure = classify_exposure(
            row["status"],
            _obj(row["metadata"]),
            _obj(row["delivery_ref"]),
            custody_refused=str(row["id"]) in refused,
        )
        exposed_at = None
        if exposure in TREATED_EXPOSURES:
            exposed_at = row["delivered_at"] or row["created_at"]
        result = await pool.execute(
            """
            INSERT INTO public.insight_outcomes (
                candidate_id, origin_butler, category, dedup_family, priority, premise,
                exposure, proposed_at, exposed_at, window_end
            ) VALUES ($1, $2, $3, $4, $5, $6::text::jsonb, $7, $8, $9, $10)
            ON CONFLICT (candidate_id) DO NOTHING
            """,
            row["id"],
            row["origin_butler"],
            row["category"],
            dedup_family(row["dedup_key"]),
            row["priority"],
            json.dumps(premise),
            exposure,
            row["created_at"],
            exposed_at,
            row["created_at"] + OBSERVATION_WINDOW,
        )
        added += int(result.split()[-1]) if result else 0
    return added


# A premise reading: ("holds" | "resolved" | "unknown", resolved_at, precision, reason).
_Reading = tuple[str, datetime | None, str | None, str | None]


async def _read_owner_condition(
    pool: asyncpg.Pool, premise: Mapping[str, Any], proposed_at: datetime
) -> _Reading:
    source, fingerprint = premise.get("source"), premise.get("fingerprint")
    if not isinstance(source, str) or not isinstance(fingerprint, str):
        return ("unknown", None, None, None)
    row = await pool.fetchrow(
        """
        SELECT state, resolved_at, metadata ->> 'resolution_reason' AS reason
        FROM public.owner_conditions
        WHERE source = $1 AND fingerprint = $2 AND first_detected_at <= $3
        ORDER BY episode DESC
        LIMIT 1
        """,
        source,
        fingerprint,
        proposed_at,
    )
    if row is None:
        return ("unknown", None, None, None)
    if row["state"] == "resolved":
        return ("resolved", row["resolved_at"], "exact", row["reason"] or "unspecified")
    return ("holds", None, None, None)


async def read_premise(
    pool: asyncpg.Pool,
    premise: Mapping[str, Any],
    proposed_at: datetime,
    *,
    now: datetime,
    finance_warning_reader: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
) -> _Reading:
    """Read one premise now. Never raises: a failed read is ``unknown``."""
    try:
        kind = premise.get("kind")
        if kind == PREMISE_KIND_OWNER_CONDITION:
            return await _read_owner_condition(pool, premise, proposed_at)
        if kind == PREMISE_KIND_PROBE:
            verdict = await check_premise(
                pool, premise, finance_warning_reader=finance_warning_reader
            )
            if verdict is True:
                return ("holds", None, None, None)
            if verdict is False:
                return ("resolved", now, "observed_by", "unspecified")
    except Exception:
        logger.warning("insight outcome: premise read failed", exc_info=True)
    return ("unknown", None, None, None)


async def sweep_outcomes(
    pool: asyncpg.Pool,
    *,
    now: datetime | None = None,
    limit: int = BATCH_LIMIT,
    finance_warning_reader: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """Settle what can be settled among open rows. Returns per-outcome counts."""
    if now is None:
        now = datetime.now(UTC)
    counts = {"checked": 0, "resolved": 0, "persisted": 0, "unmeasurable": 0, "unknown": 0}
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", _SWEEP_LOCK):
            return {**counts, "skipped": "sweep_in_progress"}
        try:
            rows = await conn.fetch(
                """
                SELECT candidate_id, premise::text AS premise, proposed_at, window_end,
                       unknown_checks, last_checked_at
                FROM public.insight_outcomes
                WHERE outcome IS NULL
                ORDER BY window_end
                LIMIT $1
                """,
                limit,
            )
            for row in rows:
                counts["checked"] += 1
                state, resolved_at, precision, reason = await read_premise(
                    pool,
                    _obj(row["premise"]),
                    row["proposed_at"],
                    now=now,
                    finance_warning_reader=finance_warning_reader,
                )
                closed = now >= row["window_end"]
                if state == "resolved" and resolved_at is not None:
                    if precision == "exact" and resolved_at > row["window_end"]:
                        outcome = "persisted"  # resolved, but only after the window closed
                    elif precision == "observed_by" and resolved_at > row["window_end"]:
                        # The probe only says it flipped since the last check. Never resolved
                        # past the window: a flip after a check that already saw the closed
                        # window holding is persisted; a flip that may straddle the window end
                        # (a late sweep, an outage, a backlog) has no knowable side.
                        last = row["last_checked_at"]
                        if last is not None and last >= row["window_end"]:
                            outcome = "persisted"
                        else:
                            outcome = "unmeasurable"
                    else:
                        outcome = "resolved"
                elif state == "holds":
                    outcome = "persisted" if closed else None
                else:
                    unknown = row["unknown_checks"] + 1
                    if unknown >= MAX_UNKNOWN_CHECKS or closed:
                        outcome = "unmeasurable"
                    else:
                        outcome = None
                    counts["unknown"] += 1
                if outcome is None:
                    await conn.execute(
                        """
                        UPDATE public.insight_outcomes
                        SET unknown_checks = unknown_checks + $2, last_checked_at = $3
                        WHERE candidate_id = $1 AND outcome IS NULL
                        """,
                        row["candidate_id"],
                        int(state == "unknown"),
                        now,
                    )
                    continue
                resolved = outcome == "resolved"
                await conn.execute(
                    """
                    UPDATE public.insight_outcomes
                    SET outcome = $2, resolved_at = $3, resolved_precision = $4,
                        resolution_reason = $5, unknown_checks = unknown_checks + $6,
                        last_checked_at = $7, settled_at = $7
                    WHERE candidate_id = $1 AND outcome IS NULL
                    """,
                    row["candidate_id"],
                    outcome,
                    resolved_at if resolved else None,
                    precision if resolved else None,
                    reason if resolved else None,
                    int(state == "unknown"),
                    now,
                )
                counts[outcome] += 1
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _SWEEP_LOCK)
    return counts


#: Settled rows per arm a class needs before it gets a verdict other than ``unmeasured``.
MIN_PER_ARM = 5
#: Settled rows are rolled up when proposed within this many days of the run.
EFFICACY_LOOKBACK = timedelta(days=90)
#: Resolve-rate gap (treated minus holdout) that reads as the nudge mattering.
EFFECTIVE_RATE_GAP = 0.2
#: Holdout resolve rate at or above which a class resolves on its own.
SELF_RESOLVING_RATE = 0.6
#: A treated median at or under this share of the holdout median reads as faster.
FASTER_MEDIAN_SHARE = 0.5
VERDICTS = ("effective", "self_resolving", "ignored", "unmeasured")
PRIORITY_BANDS = ("low", "normal", "urgent")
_NORMAL_PRIORITY_FLOOR = 50


def priority_band(priority: int) -> str:
    """Strata compared only within themselves: low under 50, urgent at the urgent threshold."""
    if priority >= URGENT_PRIORITY_THRESHOLD:
        return "urgent"
    return "normal" if priority >= _NORMAL_PRIORITY_FLOOR else "low"


def decide_verdict(
    *,
    n_treated: int,
    treated_resolved: int,
    n_holdout: int,
    holdout_resolved: int,
    treated_median_s: float | None,
    holdout_median_s: float | None,
) -> str:
    """One class's observational verdict from its two arms' settled counts.

    Fewer than :data:`MIN_PER_ARM` measured rows in either arm is ``unmeasured``. Otherwise:
    ``effective`` when the treated arm resolves clearly more often, or about as often but at
    most half as slowly; ``self_resolving`` when the holdout resolves about as often and mostly
    resolves anyway; ``ignored`` when being told did not move a premise that mostly persists.
    """
    if n_treated < MIN_PER_ARM or n_holdout < MIN_PER_ARM:
        return "unmeasured"
    treated_rate = treated_resolved / n_treated
    holdout_rate = holdout_resolved / n_holdout
    if treated_rate - holdout_rate >= EFFECTIVE_RATE_GAP:
        return "effective"
    comparable = abs(treated_rate - holdout_rate) < EFFECTIVE_RATE_GAP
    if (
        comparable
        and treated_resolved > 0
        and treated_median_s is not None
        and holdout_median_s is not None
        and treated_median_s <= holdout_median_s * FASTER_MEDIAN_SHARE
    ):
        return "effective"
    if holdout_rate >= SELF_RESOLVING_RATE:
        return "self_resolving"
    return "ignored"


async def compute_class_efficacy(pool: asyncpg.Pool, *, now: datetime | None = None) -> int:
    """Roll settled outcomes up into today's per-class, per-band verdict rows.

    Observational only: the arms are natural (a holdout is what the budget or the owner's
    context happened to hold back), so a verdict is a correlation inside one priority band, not
    a controlled effect. ``unmeasurable`` rows count in neither arm and are reported in the
    evidence. Time to resolve runs from ``proposed_at`` in both arms, because a holdout has no
    exposure time. Upserted on (class, band, bucket date), so a rerun the same day replaces the
    day's row and an earlier day's row stays as it was. Returns the rows written.
    """
    if now is None:
        now = datetime.now(UTC)
    rows = await pool.fetch(
        """
        SELECT origin_butler, category,
               CASE WHEN priority >= $3 THEN 'urgent'
                    WHEN priority >= $4 THEN 'normal' ELSE 'low' END AS band,
               count(*) FILTER (WHERE arm = 'treated' AND outcome <> 'unmeasurable')
                   AS n_treated,
               count(*) FILTER (WHERE arm = 'holdout' AND outcome <> 'unmeasurable')
                   AS n_holdout,
               count(*) FILTER (WHERE arm = 'treated' AND outcome = 'resolved')
                   AS treated_resolved,
               count(*) FILTER (WHERE arm = 'holdout' AND outcome = 'resolved')
                   AS holdout_resolved,
               count(*) FILTER (WHERE arm = 'treated' AND outcome = 'unmeasurable')
                   AS treated_unmeasurable,
               count(*) FILTER (WHERE arm = 'holdout' AND outcome = 'unmeasurable')
                   AS holdout_unmeasurable,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY resolve_s)
                   FILTER (WHERE arm = 'treated' AND outcome = 'resolved')
                   AS treated_median_s,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY resolve_s)
                   FILTER (WHERE arm = 'holdout' AND outcome = 'resolved')
                   AS holdout_median_s
        FROM (
            SELECT origin_butler, category, priority, outcome,
                   CASE WHEN exposure = ANY($5::text[]) THEN 'treated' ELSE 'holdout' END
                       AS arm,
                   extract(epoch FROM resolved_at - proposed_at)::double precision
                       AS resolve_s
            FROM public.insight_outcomes
            WHERE outcome IS NOT NULL
              AND proposed_at >= $1 AND proposed_at <= $2
              AND (exposure = ANY($5::text[]) OR exposure = ANY($6::text[]))
        ) settled
        GROUP BY origin_butler, category, band
        """,
        now - EFFICACY_LOOKBACK,
        now,
        URGENT_PRIORITY_THRESHOLD,
        _NORMAL_PRIORITY_FLOOR,
        sorted(TREATED_EXPOSURES),
        sorted(HOLDOUT_EXPOSURES),
    )
    bucket = now.astimezone(UTC).date()
    written = 0
    async with pool.acquire() as conn, conn.transaction():
        for row in rows:
            verdict = decide_verdict(
                n_treated=row["n_treated"],
                treated_resolved=row["treated_resolved"],
                n_holdout=row["n_holdout"],
                holdout_resolved=row["holdout_resolved"],
                treated_median_s=row["treated_median_s"],
                holdout_median_s=row["holdout_median_s"],
            )
            evidence = {
                "observational": True,
                "min_per_arm": MIN_PER_ARM,
                "lookback_days": EFFICACY_LOOKBACK.days,
                "treated_unmeasurable": row["treated_unmeasurable"],
                "holdout_unmeasurable": row["holdout_unmeasurable"],
                "treated_exposures": sorted(TREATED_EXPOSURES),
                "holdout_exposures": sorted(HOLDOUT_EXPOSURES),
            }
            await conn.execute(
                """
                INSERT INTO public.insight_class_efficacy (
                    origin_butler, category, priority_band, bucket_date, n_treated, n_holdout,
                    treated_resolved, holdout_resolved, treated_median_resolve_s,
                    holdout_median_resolve_s, verdict, evidence, computed_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::text::jsonb, $13)
                ON CONFLICT (origin_butler, category, priority_band, bucket_date) DO UPDATE SET
                    n_treated = EXCLUDED.n_treated, n_holdout = EXCLUDED.n_holdout,
                    treated_resolved = EXCLUDED.treated_resolved,
                    holdout_resolved = EXCLUDED.holdout_resolved,
                    treated_median_resolve_s = EXCLUDED.treated_median_resolve_s,
                    holdout_median_resolve_s = EXCLUDED.holdout_median_resolve_s,
                    verdict = EXCLUDED.verdict, evidence = EXCLUDED.evidence,
                    computed_at = EXCLUDED.computed_at
                """,
                row["origin_butler"],
                row["category"],
                row["band"],
                bucket,
                row["n_treated"],
                row["n_holdout"],
                row["treated_resolved"],
                row["holdout_resolved"],
                row["treated_median_s"],
                row["holdout_median_s"],
                verdict,
                json.dumps(evidence),
                now,
            )
            written += 1
    return written


async def run_insight_outcome_sweep(
    pool: asyncpg.Pool,
    *,
    now: datetime | None = None,
    finance_warning_reader: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """The daily job: enroll newly terminal candidates, settle open rows, then roll up verdicts.

    A sweep that raises leaves the previous efficacy rows in force (the rollup never runs), and
    a sweep skipped by a concurrent run skips the rollup too; that run writes it.
    """
    if now is None:
        now = datetime.now(UTC)
    enrolled = await enroll_terminal_candidates(pool, now=now)
    swept = await sweep_outcomes(pool, now=now, finance_warning_reader=finance_warning_reader)
    if "skipped" in swept:
        return {"enrolled": enrolled, **swept}
    efficacy = await compute_class_efficacy(pool, now=now)
    return {"enrolled": enrolled, **swept, "efficacy_rows": efficacy}
