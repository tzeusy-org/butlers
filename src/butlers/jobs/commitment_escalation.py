"""Commitment escalation — the clock and the surfacing pass for RFC 0026 commitments.

bu-n1evl / REQ-commitment-lifecycle-005, -006. A commitment is an
``owner_conditions`` row whose ``metadata->>'class'`` is ``'commitment'``
(``butlers.core.commitments``). This job is the scheduled half of that
design: it walks the commitment-class rows, moves the escalation clock,
hands the ones that have earned attention to the proactive insight engine,
and asks the owner what to do about the ones that have gone stale.

Why this job also drives the clock
----------------------------------
``condition_ledger`` advances a condition's escalation level only inside
``_confirm_episode`` — that is, only when its producer re-observes it after
``next_reescalate_at`` has passed. Every other condition family has such a
producer: ``deploy_drift`` re-surveys deployments, ``calendar_sync_deadman``
re-surveys sync freshness, so their escalation clocks are wound by the same
sweep that detects them.

A commitment has no such producer by construction (RFC 0026 §3: no scheduled
job can decide whether the owner sent Sam the book), so nothing would ever
re-observe it, so a commitment created at ``L0`` would sit at ``L0``
forever. Every requirement below it — "at L1+", "at L3 for 90 days" — would
be unreachable, and this job would be inert. REQ-commitment-lifecycle-005
opens with "A scheduled job SHALL check commitment-class ``owner_conditions``
for escalation eligibility"; :func:`_tick_escalation` is that check.

It deliberately does **not** touch ``last_confirmed_at``. Re-confirmation
means the owner or a domain butler said the commitment is still live; the
90-day garbage-collection clock (REQ-commitment-lifecycle-006) measures
exactly that silence. A job that confirmed the rows it escalated would reset
the clock it is supposed to be reading, and no commitment would ever be
collected.

Why the writes are compare-and-set, not advisory-locked
-------------------------------------------------------
The two mutating passes read the ledger with a plain ``SELECT`` and then
write each row under a ``WHERE`` clause that restates every field the
decision was made from (state, escalation level, and the exact
``next_reescalate_at`` that was read). If a domain butler's
``reconcile_snapshot`` confirmed, escalated, or resolved the row in between,
the update matches zero rows and this tick skips it — the next tick will
re-read and re-decide. Taking ``condition_ledger``'s source-scoped advisory
lock would not have helped: the read that the decision rests on happens
before the lock could be acquired, so the lock would serialize the write
while leaving the same stale-read window open. Restating the read in the
predicate closes it.

Resolution goes through the resolver, never through an observation
------------------------------------------------------------------
:func:`cancel_stale_commitment` closes a commitment through
``commitments.resolve_commitment`` — i.e. ``resolve_condition``'s
``resolution_metadata`` — so ``resolution_reason`` and ``evidence_closed``
are written by the ledger's resolution path. They are the ledger's keys, not
a producer's: ``resolve_condition`` merges creation-wins, so the same two
keys arriving on a *creation* observation would win over the closing
evidence and silently swallow it. This module therefore never builds an
``Observation`` at all, and
``tests/jobs/test_commitment_escalation.py`` pins that it never starts.

Composition with the insight engine
-----------------------------------
Surfacing is a call to ``propose_insight_candidate`` and nothing more
(REQ-commitment-lifecycle-005, RFC 0026 §7: "No changes to the insight
engine itself"). The proposer is injected rather than imported at call time
so the composition boundary is visible in the signature and testable
without an ``insight_candidates`` table; ``butlers.scheduled_jobs`` binds
the real broker function. Commitments compete for the same delivery budget,
cooldowns, and quiet hours as every other candidate.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import asyncpg

from butlers.core.commitments import (
    COMMITMENT_METADATA_CLASS,
    SURFACING_CONFIDENCE_THRESHOLD,
    resolve_commitment,
)
from butlers.core.condition_ledger import (
    # The escalation schedule itself (RFC 0026 §6 restates it as prose). Imported
    # rather than restated: a local copy would let this job's cadence and the
    # ledger's disagree silently, and the disagreement would show up as
    # commitments escalating on a different clock than every other condition.
    _ESCALATION_ADVANCE,
    ConditionTransition,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ARCHIVAL_DEDUP_SCOPE",
    "GC_STALE_DAYS",
    "INSIGHT_CATEGORY",
    "SURFACING_LEVELS",
    "EscalationResult",
    "InsightProposer",
    "cancel_stale_commitment",
    "renew_stale_commitment",
    "run_commitment_escalation",
]

_TABLE = "public.owner_conditions"
_ACTIVE_STATES = ("open", "aging")

#: Escalation levels at which a commitment is surfaced proactively. L0 is the
#: grace period — the commitment exists but has not earned attention yet
#: (RFC 0026 §6).
SURFACING_LEVELS = ("L1", "L2", "L3")

#: Insight category for every commitment candidate, surfacing and archival
#: alike, so the owner's insight history groups them.
INSIGHT_CATEGORY = "commitment"

#: Dedup-key time-scope segment for the garbage-collection proposal. Kept
#: distinct from the escalation-level scopes so an archival question is never
#: deduplicated against the L3 surfacing that preceded it.
ARCHIVAL_DEDUP_SCOPE = "archival"

#: Days at L3 without re-confirmation before the archival question is asked
#: (REQ-commitment-lifecycle-006, RFC 0026 §9).
GC_STALE_DAYS = 90

#: How long each level's candidate stays queued. One day matches the job's
#: daily cadence: a candidate this run proposed is either delivered or
#: superseded by the next run's, never left to accumulate.
_CANDIDATE_TTL = timedelta(days=1)

#: How many rows one tick will consider. Generous relative to any plausible
#: commitment population; the query orders oldest-first so that if it ever
#: does truncate, what falls off the end is the newest and least escalated,
#: never the L3 rows the collection pass exists for.
_DEFAULT_SCAN_LIMIT = 1000

#: RFC 0026 §6 escalation priorities, mapped onto RFC 0011's 1-100 scale:
#: L1 a low-urgency nudge (30-49), L2 informational (50-69), L3
#: actionable-soon (70-89). Deliberately capped below
#: ``attention_ledger.URGENT_PRIORITY_THRESHOLD`` (90): a commitment that has
#: been ignored for weeks is exactly the wrong thing to force through quiet
#: hours, and 90+ is defined as "action needed within 24-48 hours".
_PRIORITY_BY_LEVEL: dict[str, int] = {"L1": 40, "L2": 65, "L3": 85}

#: The archival question is housekeeping, not urgency — it asks the owner to
#: make a decision about something that has provably not been urgent for 90
#: days.
_ARCHIVAL_PRIORITY = 40

#: How long the ledger leaves a commitment at each level before the next due
#: transition, derived from the ledger's own schedule rather than restated:
#: ``_ESCALATION_ADVANCE`` is keyed by the level a condition is AT and yields
#: (level it moves to, interval until the FOLLOWING due date), so inverting it
#: gives the dwell time of the level being entered — L1 one day, L2 three,
#: L3 seven and repeating. Used as each candidate's cooldown so one level
#: surfaces once; the level change is what lets the next one through, because
#: it changes the dedup key.
_DWELL_DAYS_BY_LEVEL: dict[str, int] = {
    entered: interval.days for entered, interval in _ESCALATION_ADVANCE.values()
}


class InsightProposer(Protocol):
    """The one insight-engine entry point this job uses.

    Structurally identical to
    ``butlers.tools.switchboard.insight.broker.propose_insight_candidate``,
    narrowed to the arguments this job passes. Declaring it here keeps the
    composition boundary (REQ-commitment-lifecycle-005) in the signature
    instead of buried in a deferred import.
    """

    def __call__(
        self,
        pool: asyncpg.Pool,
        *,
        origin_butler: str,
        priority: int,
        category: str,
        dedup_key: str,
        message: str,
        expires_at: datetime,
        cooldown_days: int | None = None,
        channel: str | None = None,
        # A pre-serialized JSON object, not a dict, despite the broker
        # annotating this parameter ``dict``. The broker binds the value
        # straight into ``$9::jsonb``, which asyncpg infers as ``text``, so a
        # raw dict raises ``DataError`` on any pool without a dict->jsonb
        # codec registered — and this job runs against whichever pool the
        # scheduler built. A JSON string is accepted by both kinds of pool,
        # which is the same reason ``attention_ledger.record_attention_event``
        # pre-serializes. Narrowed here rather than fixed in the broker
        # because REQ-commitment-lifecycle-005 forbids insight-engine changes;
        # if the broker ever starts serializing for itself, the round-trip
        # assertion in tests/integration/test_commitment_escalation.py fails
        # rather than silently storing a double-encoded string.
        metadata: str | None = None,
        now: datetime | None = None,
    ) -> Awaitable[dict[str, str]]: ...


@dataclass(frozen=True)
class EscalationResult:
    """What one escalation tick did, for the scheduler's job record.

    ``scanned`` counts every active commitment the tick considered, so a run
    that proposes nothing is still distinguishable from a run that found
    nothing.
    """

    scanned: int = 0
    grace_shortened: int = 0
    escalated: int = 0
    surfaced: int = 0
    skipped_low_confidence: int = 0
    archival_proposed: int = 0
    proposal_errors: int = 0

    def as_dict(self) -> dict[str, int]:
        """Return the counters as a plain dict for a scheduled-job result."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


async def _load_active_commitments(pool: asyncpg.Pool, *, limit: int) -> list[dict[str, Any]]:
    """Return active commitment-class rows, oldest first.

    Commitment-ness is a metadata convention rather than a column
    (REQ-commitment-lifecycle-001), so the class filter is what makes this
    query commitment-only: a bill-overdue or spending-anomaly row from the
    same ledger is never returned (acceptance criterion 1).
    """
    rows = await pool.fetch(
        f"""
        SELECT id, source, fingerprint, state, first_detected_at,
               last_confirmed_at, next_reescalate_at, escalation_level,
               summary, metadata
        FROM {_TABLE}
        WHERE metadata->>'class' = $1
          AND state = ANY($2::text[])
        ORDER BY first_detected_at ASC
        LIMIT $3
        """,
        COMMITMENT_METADATA_CLASS,
        list(_ACTIVE_STATES),
        limit,
    )
    return [_decode_row(row) for row in rows]


def _decode_row(row: asyncpg.Record) -> dict[str, Any]:
    """Return one ledger row as a dict with ``metadata`` decoded to an object.

    Whether asyncpg hands JSONB back as ``dict`` or as raw ``str`` depends on
    whether the caller's pool registered a codec, and this job runs against
    whichever pool the scheduler built. Decoding here rather than trusting the
    pool matters more than it looks: every gate below — the confidence band,
    the deadline, the candidate metadata — reads ``metadata``, so an undecoded
    string would make every commitment look confidence-less and be silently
    skipped rather than failing loudly.

    Deliberately not ``condition_ledger``'s own row decoder: that name is
    private, and this module needs one field decoded rather than the facades'
    full row contract.
    """
    decoded = dict(row)
    raw = decoded.get("metadata")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "commitment_escalation: undecodable metadata on %s/%s — treating as empty",
                decoded.get("source"),
                decoded.get("fingerprint"),
            )
            raw = None
    decoded["metadata"] = raw if isinstance(raw, dict) else {}
    return decoded


def _metadata_of(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _confidence_of(row: dict[str, Any]) -> float | None:
    """Return the row's confidence, or ``None`` when it is missing or unusable.

    Read in Python rather than cast in SQL: a malformed ``confidence`` would
    abort a ``::numeric`` cast for the whole result set, taking every
    well-formed commitment down with it.
    """
    value = _metadata_of(row).get("confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _deadline_of(row: dict[str, Any]) -> datetime | None:
    """Return the row's deadline as an aware datetime, or ``None``.

    ``create_commitment`` stores an ISO-8601 string. A naive value is read as
    UTC so it can be compared against ``next_reescalate_at``, which the
    ledger always writes aware.
    """
    raw = _metadata_of(row).get("deadline")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        logger.warning(
            "commitment_escalation: unparseable deadline %r on %s/%s — ignoring",
            raw,
            row["source"],
            row["fingerprint"],
        )
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _origin_butler(source: str) -> str:
    """Return the butler half of a ``"{origin_butler}:{category}"`` source.

    The owner-condition source convention puts the producing butler first, so
    a commitment insight is attributed to the butler that filed it rather
    than to whoever runs this job.
    """
    return source.split(":", 1)[0] or "switchboard"


# ---------------------------------------------------------------------------
# Pass 1: deadline-aware grace shortening
# ---------------------------------------------------------------------------


async def _shorten_grace_for_deadlines(
    pool: asyncpg.Pool, rows: list[dict[str, Any]], *, now: datetime
) -> int:
    """Collapse the L0 grace of any commitment whose deadline falls inside it.

    REQ-commitment-lifecycle-005: "WHEN a commitment has a deadline within
    its L0 grace period THEN the L0 grace period is shortened so the
    commitment is surfaced at L1 before the deadline passes."

    The grace collapses to ``now`` rather than to some margin before the
    deadline, because this job ticks on a daily cadence: any later target
    risks the next tick landing on the far side of the deadline, which is
    the one outcome the requirement forbids. A deadline inside the grace
    window means the window was already too long, so the commitment surfaces
    on this tick — :func:`_tick_escalation` runs immediately after and finds
    it due.

    Only L0 rows are eligible; past L0 the grace period no longer exists and
    the escalation schedule owns the cadence.
    """
    shortened = 0
    for row in rows:
        if row["escalation_level"] != "L0":
            continue
        next_due = row["next_reescalate_at"]
        if next_due is None or next_due <= now:
            continue
        deadline = _deadline_of(row)
        if deadline is None or deadline > next_due:
            continue

        updated = await pool.execute(
            f"""
            UPDATE {_TABLE}
            SET next_reescalate_at = $2
            WHERE id = $1
              AND state = ANY($3::text[])
              AND escalation_level = 'L0'
              AND next_reescalate_at = $4
            """,
            row["id"],
            now,
            list(_ACTIVE_STATES),
            next_due,
        )
        if updated.endswith(" 0"):
            continue
        row["next_reescalate_at"] = now
        shortened += 1
        logger.info(
            "commitment_escalation: deadline %s inside grace — surfacing %s/%s now",
            deadline.isoformat(),
            row["source"],
            row["fingerprint"],
        )
    return shortened


# ---------------------------------------------------------------------------
# Pass 2: the escalation clock
# ---------------------------------------------------------------------------


async def _tick_escalation(pool: asyncpg.Pool, rows: list[dict[str, Any]], *, now: datetime) -> int:
    """Advance every commitment whose next escalation is due.

    Mirrors ``condition_ledger._confirm_episode``'s due branch — same
    schedule table, same ``aging`` state, same ``last_escalated_at`` /
    ``next_reescalate_at`` bookkeeping — with one deliberate omission:
    ``last_confirmed_at`` is left alone. See this module's docstring for why
    that omission is the whole point.
    """
    escalated = 0
    for row in rows:
        next_due = row["next_reescalate_at"]
        if next_due is None or next_due > now:
            continue
        current_level = row["escalation_level"]
        advance = _ESCALATION_ADVANCE.get(current_level)
        if advance is None:
            logger.warning(
                "commitment_escalation: unknown escalation level %r on %s/%s — skipping",
                current_level,
                row["source"],
                row["fingerprint"],
            )
            continue
        new_level, interval_to_next = advance

        updated = await pool.execute(
            f"""
            UPDATE {_TABLE}
            SET escalation_level = $2,
                state = 'aging',
                last_escalated_at = $3,
                next_reescalate_at = $3::timestamptz + $4
            WHERE id = $1
              AND state = ANY($5::text[])
              AND escalation_level = $6
              AND next_reescalate_at = $7
            """,
            row["id"],
            new_level,
            now,
            interval_to_next,
            list(_ACTIVE_STATES),
            current_level,
            next_due,
        )
        if updated.endswith(" 0"):
            continue
        row["escalation_level"] = new_level
        row["state"] = "aging"
        row["next_reescalate_at"] = now + interval_to_next
        escalated += 1
    return escalated


# ---------------------------------------------------------------------------
# Pass 3: surfacing
# ---------------------------------------------------------------------------


def _dedup_key(fingerprint: str, scope: str) -> str:
    """Return the insight dedup key for one commitment at one scope.

    Three segments — ``{category}:{entity}:{time-scope}`` — because that is
    the broker's required shape (``insight.models._DEDUP_KEY_PATTERN``); a
    two-segment ``commitment:{fingerprint}`` is rejected outright. Scoping by
    escalation level rather than pinning one key per commitment is what lets
    L2's elevated candidate through after L1's cooldown, exactly as
    ``flight_status`` buckets by status so a status flip re-notifies.
    """
    return f"{INSIGHT_CATEGORY}:{fingerprint}:{scope}"


def _candidate_metadata(row: dict[str, Any], *, proposal: str) -> dict[str, Any]:
    """Return the structured identity a responder needs to act on the insight."""
    metadata = _metadata_of(row)
    return {
        "class": COMMITMENT_METADATA_CLASS,
        "proposal": proposal,
        "source": row["source"],
        "fingerprint": row["fingerprint"],
        "escalation_level": row["escalation_level"],
        "kind": metadata.get("kind"),
        "direction": metadata.get("direction"),
        "counterparty_entity_id": metadata.get("counterparty_entity_id"),
    }


def _summary_of(row: dict[str, Any]) -> str:
    summary = (row.get("summary") or "").strip()
    return summary or "Untitled commitment"


def _surfacing_message(row: dict[str, Any], *, now: datetime) -> str:
    """Return the owner-facing prose for one escalating commitment."""
    parts = [_summary_of(row)]
    deadline = _deadline_of(row)
    if deadline is not None:
        parts.append(f"Deadline {deadline.date().isoformat()}.")
    days_open = max((now - row["first_detected_at"]).days, 0)
    parts.append(f"Open {days_open}d at {row['escalation_level']}.")
    return " ".join(parts)


def _archival_message(row: dict[str, Any], *, now: datetime) -> str:
    """Return the garbage-collection question, worded as RFC 0026 §9 words it.

    ``N`` counts the days since the last re-confirmation rather than since
    creation: silence is what triggered the question, so silence is the
    number that explains it.
    """
    days = max((now - row["last_confirmed_at"]).days, 0)
    return (
        f"This commitment has been open for {days} days with no activity. "
        f"Cancel or keep? ({_summary_of(row)})"
    )


async def _propose(
    pool: asyncpg.Pool,
    insight_proposer: InsightProposer,
    *,
    row: dict[str, Any],
    priority: int,
    dedup_key: str,
    message: str,
    cooldown_days: int,
    proposal: str,
    now: datetime,
) -> str:
    """Submit one candidate and return the broker's status verdict.

    ``"accepted"``, ``"filtered"`` (the owner has insights turned off — not a
    fault), or ``"error"``. A rejection is logged and counted, never raised:
    one unroutable commitment must not stop the tick from escalating the
    rest.
    """
    try:
        result = await insight_proposer(
            pool,
            origin_butler=_origin_butler(row["source"]),
            priority=priority,
            category=INSIGHT_CATEGORY,
            dedup_key=dedup_key,
            message=message,
            expires_at=now + _CANDIDATE_TTL,
            cooldown_days=cooldown_days,
            metadata=json.dumps(_candidate_metadata(row, proposal=proposal)),
            # This tick reads its clock once, from Postgres, and derives every
            # timestamp from it; the broker otherwise checks freshness against
            # its own wall clock, so any skew between the two would reject a
            # candidate this job had just built. See the broker's `now` docs.
            now=now,
        )
    except Exception:
        logger.exception(
            "commitment_escalation: proposing %s for %s/%s raised",
            proposal,
            row["source"],
            row["fingerprint"],
        )
        return "error"

    status = (result or {}).get("status", "error")
    if status == "error":
        logger.warning(
            "commitment_escalation: broker rejected %s for %s/%s: %s",
            proposal,
            row["source"],
            row["fingerprint"],
            (result or {}).get("reason", "unknown"),
        )
    return status


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------


async def run_commitment_escalation(
    pool: asyncpg.Pool,
    *,
    insight_proposer: InsightProposer,
    limit: int = _DEFAULT_SCAN_LIMIT,
) -> EscalationResult:
    """Run one commitment escalation tick.

    In order: shorten the grace of any deadline-bearing commitment whose
    deadline falls inside it, advance every commitment whose escalation is
    due, propose an insight for each surfacing-eligible commitment at L1+,
    and ask the archival question for each commitment that has sat at L3 for
    ``GC_STALE_DAYS`` without re-confirmation. The order matters: a
    commitment whose grace collapses on this tick escalates on this tick and
    is surfaced on this tick.

    Commitments below :data:`~butlers.core.commitments.SURFACING_CONFIDENCE_THRESHOLD`
    are counted and skipped, never proposed
    (REQ-commitment-lifecycle-004): they remain visible to the dashboard and
    prep-card queries, and their escalation clock still runs, but they are
    never pushed at the owner.

    Never raises for a single bad row — a malformed deadline, an unknown
    escalation level, or a broker rejection is logged and counted so one
    commitment cannot stop the sweep.
    """
    now: datetime = await pool.fetchval("SELECT now()")
    rows = await _load_active_commitments(pool, limit=limit)

    grace_shortened = await _shorten_grace_for_deadlines(pool, rows, now=now)
    escalated = await _tick_escalation(pool, rows, now=now)

    surfaced = 0
    skipped_low_confidence = 0
    archival_proposed = 0
    proposal_errors = 0
    stale_before = now - timedelta(days=GC_STALE_DAYS)

    for row in rows:
        level = row["escalation_level"]
        if level not in SURFACING_LEVELS:
            continue

        confidence = _confidence_of(row)
        if confidence is None or confidence < SURFACING_CONFIDENCE_THRESHOLD:
            skipped_low_confidence += 1
        else:
            status = await _propose(
                pool,
                insight_proposer,
                row=row,
                priority=_PRIORITY_BY_LEVEL[level],
                dedup_key=_dedup_key(row["fingerprint"], level),
                message=_surfacing_message(row, now=now),
                cooldown_days=_DWELL_DAYS_BY_LEVEL[level],
                proposal="surfacing",
                now=now,
            )
            if status == "accepted":
                surfaced += 1
            elif status == "error":
                proposal_errors += 1

        # Collection is not surfacing, so it is deliberately outside the
        # confidence gate. REQ-commitment-lifecycle-006 states the 90-day rule
        # unconditionally, and the reading that matters is the outcome: gating
        # it on confidence would make the least-trustworthy rows — the hedged
        # guesses the extraction pipeline is explicitly allowed to file
        # (RFC 0026 §8) — the only ones that can never be collected, in a pass
        # whose entire purpose is to bound the ledger's growth. The two
        # proposals also ask different questions: surfacing nudges the owner
        # about an obligation, while this asks whether a record should still
        # exist.
        if level != "L3" or row["last_confirmed_at"] > stale_before:
            continue

        status = await _propose(
            pool,
            insight_proposer,
            row=row,
            priority=_ARCHIVAL_PRIORITY,
            dedup_key=_dedup_key(row["fingerprint"], ARCHIVAL_DEDUP_SCOPE),
            message=_archival_message(row, now=now),
            cooldown_days=_DWELL_DAYS_BY_LEVEL["L3"],
            proposal="archival",
            now=now,
        )
        if status == "accepted":
            archival_proposed += 1
        elif status == "error":
            proposal_errors += 1

    return EscalationResult(
        scanned=len(rows),
        grace_shortened=grace_shortened,
        escalated=escalated,
        surfaced=surfaced,
        skipped_low_confidence=skipped_low_confidence,
        archival_proposed=archival_proposed,
        proposal_errors=proposal_errors,
    )


# ---------------------------------------------------------------------------
# The archival proposal's two outcomes
# ---------------------------------------------------------------------------


async def cancel_stale_commitment(
    pool: asyncpg.Pool,
    *,
    source: str,
    fingerprint: str,
    detail: str | None = None,
    session_id: str | None = None,
) -> ConditionTransition | None:
    """Close a commitment the owner answered the archival question by cancelling.

    REQ-commitment-lifecycle-006: resolves with ``resolution_reason:
    "cancelled"`` and ``evidence_closed.source: "owner_confirmed"`` — the
    owner said so, which is the strongest closure evidence RFC 0026 §3
    recognises.

    Both keys travel as ``resolution_metadata`` through
    ``resolve_commitment``, never as observation metadata. That is not a
    stylistic preference: ``resolve_condition`` merges creation-wins, so a
    ``resolution_reason`` already sitting on the row from creation time would
    beat the one written here and the cancellation would record whatever the
    producer happened to put there.

    Returns the resolved transition, or ``None`` when the commitment has no
    active episode — already resolved, or never observed.
    """
    evidence_closed: dict[str, Any] = {
        "source": "owner_confirmed",
        "detail": detail or "owner cancelled a stale commitment at archival review",
    }
    if session_id is not None:
        evidence_closed["session_id"] = session_id

    return await resolve_commitment(
        pool,
        source=source,
        fingerprint=fingerprint,
        resolution_reason="cancelled",
        evidence_closed=evidence_closed,
    )


async def renew_stale_commitment(pool: asyncpg.Pool, *, source: str, fingerprint: str) -> bool:
    """Reset a commitment the owner answered the archival question by keeping.

    REQ-commitment-lifecycle-006: "Renewal resets escalation to L1 for
    another cycle." Written directly rather than through
    ``reconcile_snapshot``, because the ledger's confirmation path only ever
    advances a level — an L3 commitment re-observed is still L3 — and there
    is no downward transition to borrow.

    ``last_confirmed_at`` moves to now, which is the substance of the
    renewal: the owner has just said the commitment is live, so the 90-day
    silence clock restarts from this moment. Escalation resumes from L1 on
    the ledger's normal schedule.

    Returns ``False`` when no active commitment matched, so a caller can
    distinguish "renewed" from "the owner answered a question about a
    commitment that had already resolved".
    """
    interval = timedelta(days=_DWELL_DAYS_BY_LEVEL["L1"])
    updated = await pool.execute(
        f"""
        UPDATE {_TABLE}
        SET escalation_level = 'L1',
            state = 'aging',
            last_confirmed_at = now(),
            last_escalated_at = now(),
            next_reescalate_at = now() + $3
        WHERE source = $1
          AND fingerprint = $2
          AND state = ANY($4::text[])
          AND metadata->>'class' = $5
        """,
        source,
        fingerprint,
        interval,
        list(_ACTIVE_STATES),
        COMMITMENT_METADATA_CLASS,
    )
    return not updated.endswith(" 0")
