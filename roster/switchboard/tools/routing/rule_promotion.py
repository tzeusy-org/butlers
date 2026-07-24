"""Rule promotion trigger: mining ``routing_verdict_log`` into suggestions.

bu-wuwy9 (rule-promotion bead 3 of 7). Per
``docs/plans/2026-07-06-switchboard-rule-promotion-design.md`` section 2 and
the merged openspec change ``switchboard-rule-promotion``
(``openspec/changes/switchboard-rule-promotion/specs/switchboard-rule-promotion/spec.md``,
"Requirement: Promotion Trigger" / "Requirement: Clearly-Automated Sender
Classification"). Bead 1 (sw_019, PR #2983) built the ``routing_verdict_log``
mining substrate; bead 2 (sw_020, PR #2992) built the
``rule_promotion_suggestions`` schema. This bead is the periodic scan that
turns repeated LLM agreement into a ``pending_review`` suggestion row. It
never mints ``ingestion_rules`` directly — that only happens on explicit
owner confirmation (bead 4, out of scope here).

Evidence-quality gate — UTC anchoring + elapsed-time floor
-----------------------------------------------------------
The design doc flagged, but deliberately did not prescribe, how to close a
gaming hole in a naive "N distinct calendar days" check: two verdicts a few
minutes apart that straddle a UTC midnight boundary (e.g. 23:59 and 00:01)
satisfy a bare ``decided_at::date`` distinct-count check while being the
*exact* single-burst evidence shape the gate exists to reject. This bead's
resolution (design.md "D5", spec's "Requirement: Promotion Trigger"): keep
the calendar-day framing (``>= 2`` distinct UTC calendar dates among the
evidence window), but ALSO require a minimum elapsed-time floor between the
oldest and newest evidence timestamp (default 20 hours — the exact value the
design doc itself proposed as an example). Both checks are mandatory and
independent:

- distinct-day count alone rejects same-day bursts (1 distinct day).
- the elapsed floor alone rejects midnight-adjacent bursts (2 distinct days,
  but only minutes apart) — this is the check that actually closes the
  gaming hole; the distinct-day count by itself does not.

The alternative the design doc offered (drop calendar-day framing entirely,
keep only a pure elapsed-time floor) was not chosen, because the spec's own
scenarios ("Promotion-eligible pattern creates a suggestion") assert on the
distinct-day count directly ("3 different calendar days") — keeping that
framing preserves the intuitive "spread across N days" read for anyone
inspecting a suggestion's evidence later, at the cost of needing both checks
instead of one.

Automated-sender classification
--------------------------------
Reuses the exact bulk-mail signal vocabulary already seeded as
``ingestion_rules`` in migration ``003_switchboard_routing.py`` (not a new
heuristic): ``List-Unsubscribe`` header present, ``Precedence: bulk`` or
``Precedence: list``, ``Auto-Submitted: auto-generated``, or a sender
local-part matching the ``noreply``/``no-reply``/``notifications``/``alerts``
prefix convention. The local-part check is address-shaped and evaluated
directly against ``sender_key`` (no header evidence needed — a
``noreply@...`` address is automated regardless of what any given message's
headers say). The header-based checks require **every** evidence event
backing the suggestion to show at least one bulk-mail signal (conservative
"all", not "any") — the spec's scenarios only pin down the "all automated"
and "none automated" endpoints; requiring consistency across the whole
evidence window avoids one automated-looking outlier flipping the flag for
an otherwise-human sender, which matters because ``is_clearly_automated``
suggestions get a lower-scrutiny batched confirm path (design.md section 3).

Address-level only (bead 3 scope)
-----------------------------------
Per design.md's "Proposed implementation beads" / "Expected impact":
domain-level rollup (``sender_domain``) is explicitly deferred to a future
bead ("ship address-level mining first ... treat domain-level rollup as the
very next iteration"). This module only ever proposes
``proposed_rule_type='sender_address'`` suggestions.

Bounded scan windows
---------------------
The candidate scan is bounded to ``routing_verdict_log`` rows with
``verdict_source='llm'`` within a lookback window (default 30 days, via the
``ix_routing_verdict_log_llm_only`` partial index), not the full table
history. Pending promotion suggestions form a separate, indexed lifecycle
worklist so they can still be reconciled if their original evidence ages out
of that lookback. Per-candidate evidence lookups are ``LIMIT``-bounded to the
configured threshold and use the ``(sender_key, source_channel, decided_at
DESC)`` index. Nothing in this module scans the full verdict history on every
run.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import asyncpg

from butlers.ingestion_policy import IngestionEnvelope, IngestionPolicyEvaluator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults (all overridable via job_args — see roster/switchboard/jobs/
# rule_promotion_trigger.py)
# ---------------------------------------------------------------------------

DEFAULT_PROMOTION_THRESHOLD = 3
"""N-consecutive-same-verdict threshold (design.md section 2 default)."""

DEFAULT_MIN_DISTINCT_DAYS = 2
"""Minimum distinct UTC calendar days the evidence window must span."""

DEFAULT_MIN_ELAPSED_FLOOR = timedelta(hours=20)
"""Minimum elapsed time between oldest/newest evidence — closes the
midnight-boundary gaming hole design.md flagged (D5)."""

DEFAULT_LOOKBACK_WINDOW = timedelta(days=30)
"""Bounds the candidate-sender scan to recent LLM verdicts only."""

_ROUTE_TO_PREFIX = "route_to:"

# A pending promotion becomes obsolete when the production policy evaluator
# finds an enabled rule that already takes precedence for the same source.
# Keep this system actor explicit in the durable audit row; it is neither an
# owner dismissal nor an auto-applied promotion.
SUPERSEDED_BY_COVERAGE_ACTOR = "system:rule_promotion_trigger"

_AUTOMATED_LOCAL_PART_PREFIXES = ("noreply", "no-reply", "notifications", "alerts")

# Verdict actions with no meaningful standing ingestion_rules equivalent to
# promote. 'pass_through' means "no action taken" (nothing to encode as a
# rule); 'block' is connector-scope-only and never appears for scope='global'
# LLM verdicts. Neither is currently written by pipeline.py's LLM verdict
# hook (only 'route_to' is, as of bu-aga08), but this guard makes the
# contract explicit rather than accidental.
_NO_RULE_EQUIVALENT_ACTIONS = frozenset({"pass_through", "block"})


# ---------------------------------------------------------------------------
# Pure helpers — unit-tested directly, no DB required.
# ---------------------------------------------------------------------------


def as_utc(ts: datetime) -> datetime:
    """Coerce *ts* to a UTC-aware datetime.

    ``routing_verdict_log.decided_at`` is ``TIMESTAMPTZ`` — asyncpg returns
    timezone-aware datetimes for it in practice, but this defends against a
    naive datetime (e.g. constructed directly in a test) by assuming UTC
    rather than raising.
    """
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def verdicts_agree(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[str, str | None] | None:
    """Return the common ``(verdict_action, verdict_target)`` pair if every
    row in *rows* agrees, else ``None``. Empty input is not agreement."""
    if not rows:
        return None
    first = (rows[0]["verdict_action"], rows[0]["verdict_target"])
    for row in rows[1:]:
        if (row["verdict_action"], row["verdict_target"]) != first:
            return None
    return first


def distinct_utc_calendar_days(timestamps: Sequence[datetime]) -> int:
    """Count distinct UTC calendar dates among *timestamps*."""
    return len({as_utc(ts).date() for ts in timestamps})


def passes_evidence_quality_gate(
    timestamps: Sequence[datetime],
    *,
    min_distinct_days: int = DEFAULT_MIN_DISTINCT_DAYS,
    min_elapsed: timedelta = DEFAULT_MIN_ELAPSED_FLOOR,
) -> bool:
    """Mandatory evidence-quality gate for the promotion trigger.

    Both checks must pass:

    - the evidence timestamps span at least ``min_distinct_days`` distinct
      UTC calendar dates;
    - the elapsed time between the oldest and newest timestamp is at least
      ``min_elapsed``.

    The second check is what actually rejects a midnight-adjacent burst
    (e.g. 23:59 and 00:01) that would otherwise satisfy the first check on
    single-burst evidence — see the module docstring.
    """
    if len(timestamps) < 2:
        return False
    utc_timestamps = [as_utc(ts) for ts in timestamps]
    if distinct_utc_calendar_days(utc_timestamps) < min_distinct_days:
        return False
    elapsed = max(utc_timestamps) - min(utc_timestamps)
    return elapsed >= min_elapsed


def build_proposed_action(verdict_action: str, verdict_target: str | None) -> str | None:
    """Translate a ``routing_verdict_log`` verdict into a
    ``rule_promotion_suggestions.proposed_action`` string.

    Mirrors the inverse of ``butlers.ingestion_policy._parse_action``
    (duplicated rather than imported — that function is a private module
    internal, and this bead's inverse-mapping is a handful of lines with a
    different failure contract: it returns ``None`` for verdict actions with
    no rule equivalent instead of raising).

    Returns ``None`` for ``pass_through``/``block`` verdicts — nothing
    meaningful to promote.
    """
    if verdict_action == "route_to":
        if not verdict_target:
            return None
        return f"{_ROUTE_TO_PREFIX}{verdict_target}"
    if verdict_action in ("skip", "metadata_only"):
        return verdict_action
    return None


def parse_proposed_action(proposed_action: str) -> tuple[str, str | None]:
    """Inverse of :func:`build_proposed_action`."""
    if proposed_action.startswith(_ROUTE_TO_PREFIX):
        target = proposed_action[len(_ROUTE_TO_PREFIX) :]
        return "route_to", target or None
    return proposed_action, None


def _local_part(sender_key: str) -> str:
    return sender_key.split("@", 1)[0].lower() if "@" in sender_key else sender_key.lower()


def _headers_show_bulk_signal(headers: Mapping[str, str]) -> bool:
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    if "list-unsubscribe" in lowered:
        return True
    precedence = lowered.get("precedence", "").strip().lower()
    if precedence in ("bulk", "list"):
        return True
    auto_submitted = lowered.get("auto-submitted", "").strip().lower()
    if auto_submitted == "auto-generated":
        return True
    return False


def compute_is_clearly_automated(
    sender_key: str,
    evidence_headers: Sequence[Mapping[str, str]],
) -> bool:
    """Classify whether the evidence backing a suggestion looks automated.

    See the module docstring's "Automated-sender classification" section for
    the full rationale (reused header vocabulary from migration 003, "all"
    not "any" for the header-based checks).
    """
    if _local_part(sender_key).startswith(_AUTOMATED_LOCAL_PART_PREFIXES):
        return True
    if not evidence_headers:
        return False
    return all(_headers_show_bulk_signal(h) for h in evidence_headers)


# ---------------------------------------------------------------------------
# Evaluator seam — allows unit tests to inject a fake without a live DB.
# ---------------------------------------------------------------------------


class _CoverageEvaluator(Protocol):
    async def ensure_loaded(self) -> None: ...

    def evaluate(self, envelope: IngestionEnvelope) -> Any: ...


@dataclass
class PromotionTriggerResult:
    """Summary counters returned by :func:`run_rule_promotion_trigger`."""

    candidates_scanned: int = 0
    suggestions_created: int = 0
    suggestions_bumped: int = 0
    suggestions_superseded: int = 0
    skipped_existing_rule: int = 0
    skipped_dismissal_cooldown: int = 0
    skipped_insufficient_evidence: int = 0
    skipped_no_agreement: int = 0
    skipped_no_rule_equivalent: int = 0
    skipped_no_new_evidence: int = 0
    skipped_race_conflict: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "candidates_scanned": self.candidates_scanned,
            "suggestions_created": self.suggestions_created,
            "suggestions_bumped": self.suggestions_bumped,
            "suggestions_superseded": self.suggestions_superseded,
            "skipped_existing_rule": self.skipped_existing_rule,
            "skipped_dismissal_cooldown": self.skipped_dismissal_cooldown,
            "skipped_insufficient_evidence": self.skipped_insufficient_evidence,
            "skipped_no_agreement": self.skipped_no_agreement,
            "skipped_no_rule_equivalent": self.skipped_no_rule_equivalent,
            "skipped_no_new_evidence": self.skipped_no_new_evidence,
            "skipped_race_conflict": self.skipped_race_conflict,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# DB-backed helpers — each a small, independently patchable seam for tests.
# ---------------------------------------------------------------------------


async def _fetch_candidates(pool: asyncpg.Pool, *, since: datetime) -> list[asyncpg.Record]:
    """Distinct (sender_key, source_channel) pairs that need reconciliation.

    Recent LLM verdicts drive new promotions and evidence bumps, bounded by
    the lookback window and the ``verdict_source='llm'`` partial index. The
    pending-promotion worklist uses its own partial unique index so an older
    still-actionable suggestion remains eligible for a coverage recheck.
    """
    return await pool.fetch(
        """
        SELECT sender_key, source_channel
        FROM switchboard.routing_verdict_log
        WHERE verdict_source = 'llm' AND decided_at >= $1
        UNION
        SELECT sender_key, source_channel
        FROM switchboard.rule_promotion_suggestions
        WHERE status = 'pending_review' AND suggestion_kind = 'promotion'
        """,
        since,
    )


async def _fetch_pending_suggestion(
    pool: asyncpg.Pool, *, sender_key: str, source_channel: str
) -> asyncpg.Record | None:
    """The at-most-one pending promotion suggestion for this sender/channel."""
    return await pool.fetchrow(
        """
        SELECT id, evidence_count, last_evidence_at, proposed_action
        FROM switchboard.rule_promotion_suggestions
        WHERE sender_key = $1 AND source_channel = $2
          AND status = 'pending_review' AND suggestion_kind = 'promotion'
        """,
        sender_key,
        source_channel,
    )


async def _fetch_active_dismissal_cooldown(
    pool: asyncpg.Pool, *, sender_key: str, source_channel: str
) -> asyncpg.Record | None:
    """The most recent still-active owner dismissal cooldown for this
    sender/channel, or ``None`` if there is no unexpired dismissal.

    When the owner dismisses a promotion suggestion (approvals surface,
    ``POST .../dismiss``), the row goes ``status='dismissed'`` and records a
    ``cooldown_until`` window. That dismissed row is invisible to
    :func:`_fetch_pending_suggestion` (it only looks at ``pending_review``),
    so without this check the very next scan would re-propose the same
    sender/channel and the dismissal would be cosmetic — the exact recurrence
    the owner is trying to stop by clicking Dismiss. Honoring the cooldown
    here makes Dismiss mean "leave this sender/channel alone until the window
    lapses"; after it expires (or if the pattern is later covered by a rule),
    normal proposal resumes.

    Scoped to ``(sender_key, source_channel)`` — matching the pending-promotion
    uniqueness granularity — rather than the specific ``proposed_action``: a
    dismissal is the owner declining to promote *this sender on this channel*,
    not merely declining one particular target.
    """
    return await pool.fetchrow(
        """
        SELECT id, cooldown_until
        FROM switchboard.rule_promotion_suggestions
        WHERE sender_key = $1 AND source_channel = $2
          AND suggestion_kind = 'promotion'
          AND status = 'dismissed'
          AND cooldown_until IS NOT NULL
          AND cooldown_until > now()
        ORDER BY cooldown_until DESC
        LIMIT 1
        """,
        sender_key,
        source_channel,
    )


async def _fetch_latest_llm_verdicts(
    pool: asyncpg.Pool, *, sender_key: str, source_channel: str, limit: int
) -> list[asyncpg.Record]:
    """The most recent *limit* LLM verdicts for this sender/channel.

    ``LIMIT``-bounded, uses the ``(sender_key, source_channel,
    decided_at DESC)`` index — this is the "N-consecutive-same-verdict" scan.
    """
    return await pool.fetch(
        """
        SELECT decided_at, verdict_action, verdict_target, ingestion_event_id
        FROM switchboard.routing_verdict_log
        WHERE sender_key = $1 AND source_channel = $2 AND verdict_source = 'llm'
        ORDER BY decided_at DESC
        LIMIT $3
        """,
        sender_key,
        source_channel,
        limit,
    )


async def _fetch_new_agreeing_verdicts(
    pool: asyncpg.Pool,
    *,
    sender_key: str,
    source_channel: str,
    verdict_action: str,
    verdict_target: str | None,
    after: datetime,
) -> list[asyncpg.Record]:
    """LLM verdicts newer than *after* that still agree with the suggestion's
    original proposal — the "bump" query for an existing pending suggestion."""
    return await pool.fetch(
        """
        SELECT decided_at
        FROM switchboard.routing_verdict_log
        WHERE sender_key = $1 AND source_channel = $2 AND verdict_source = 'llm'
          AND decided_at > $3
          AND verdict_action = $4
          AND verdict_target IS NOT DISTINCT FROM $5
        """,
        sender_key,
        source_channel,
        after,
        verdict_action,
        verdict_target,
    )


async def _fetch_evidence_headers(
    pool: asyncpg.Pool, evidence_rows: Sequence[asyncpg.Record]
) -> list[dict[str, str]]:
    """Best-effort fetch of the email headers backing each evidence row.

    Joins ``routing_verdict_log.ingestion_event_id`` to
    ``message_inbox.id`` (the same UUID, written in the same transaction as
    ``public.ingestion_events`` — see ``roster/switchboard/tools/ingestion/
    ingest.py``) to read ``raw_payload -> payload -> raw -> headers``, the
    same path ``_make_ingestion_envelope`` reads at ingest time. Bounded by a
    ``received_at`` range derived from the evidence timestamps (message_inbox
    is partitioned by ``received_at``) to help partition pruning, rather than
    an unbounded ``id = ANY(...)`` scan across every partition.

    Non-email channels (or events with no captured headers) simply
    contribute no headers — the classifier treats an empty headers list as
    "no header-based signal", falling back to the local-part check.
    """
    event_ids = [r["ingestion_event_id"] for r in evidence_rows if r["ingestion_event_id"]]
    if not event_ids:
        return []
    timestamps = [as_utc(r["decided_at"]) for r in evidence_rows]
    lower = min(timestamps) - timedelta(days=1)
    upper = max(timestamps) + timedelta(days=1)
    rows = await pool.fetch(
        """
        SELECT raw_payload #> '{payload,raw,headers}' AS headers
        FROM switchboard.message_inbox
        WHERE id = ANY($1::uuid[]) AND received_at BETWEEN $2 AND $3
        """,
        event_ids,
        lower,
        upper,
    )
    headers_list: list[dict[str, str]] = []
    for row in rows:
        raw = row["headers"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                raw = None
        if isinstance(raw, dict):
            headers_list.append({str(k): str(v) for k, v in raw.items()})
    return headers_list


async def _insert_suggestion(
    pool: asyncpg.Pool,
    *,
    sender_key: str,
    source_channel: str,
    proposed_condition: dict[str, Any],
    proposed_action: str,
    evidence_count: int,
    first_evidence_at: datetime,
    last_evidence_at: datetime,
    is_clearly_automated: bool,
) -> Any:
    """Insert a pending promotion suggestion.

    Uses ``ON CONFLICT ... DO NOTHING`` against the unique partial index
    (``ux_rule_promotion_suggestions_pending_promotion``) so a race against a
    concurrent job run (or a suggestion created between this candidate's
    pending-check and this insert) is a silent no-op, not an error — the
    next scan picks it up as a bump instead of double-proposing.

    ``proposed_condition`` is passed as a plain dict, matching this
    codebase's convention for jsonb parameters (see
    ``roster/switchboard/tools/ingestion/ingest.py``'s ``message_inbox``/
    ``ingestion_events`` inserts) — the production pool registers a JSONB
    codec (``butlers.db.register_jsonb_codec``) that encodes/decodes dicts
    transparently; no manual ``json.dumps``/``::jsonb`` cast needed.
    """
    return await pool.fetchval(
        """
        INSERT INTO switchboard.rule_promotion_suggestions
            (suggestion_kind, sender_key, source_channel, proposed_rule_type,
             proposed_condition, proposed_action, evidence_count,
             first_evidence_at, last_evidence_at, is_clearly_automated, status)
        VALUES
            ('promotion', $1, $2, 'sender_address', $3, $4, $5, $6, $7, $8,
             'pending_review')
        ON CONFLICT (sender_key, source_channel)
            WHERE status = 'pending_review' AND suggestion_kind = 'promotion'
        DO NOTHING
        RETURNING id
        """,
        sender_key,
        source_channel,
        proposed_condition,
        proposed_action,
        evidence_count,
        first_evidence_at,
        last_evidence_at,
        is_clearly_automated,
    )


async def _bump_suggestion(
    pool: asyncpg.Pool, *, suggestion_id: Any, evidence_count_delta: int, last_evidence_at: datetime
) -> None:
    await pool.execute(
        """
        UPDATE switchboard.rule_promotion_suggestions
        SET evidence_count = evidence_count + $1, last_evidence_at = $2
        WHERE id = $3
        """,
        evidence_count_delta,
        last_evidence_at,
        suggestion_id,
    )


async def _supersede_suggestion(pool: asyncpg.Pool, *, suggestion_id: Any) -> bool:
    """Mark a still-pending suggestion obsolete after a coverage recheck.

    The status predicate makes this transition race-safe: a concurrent owner
    confirm/dismiss (or another trigger run) cannot be overwritten once it has
    made the suggestion terminal.
    """
    result = await pool.execute(
        """
        UPDATE switchboard.rule_promotion_suggestions
        SET status = 'superseded',
            decided_at = now(),
            decided_by = $1
        WHERE id = $2
          AND status = 'pending_review'
        """,
        SUPERSEDED_BY_COVERAGE_ACTOR,
        suggestion_id,
    )
    return result == "UPDATE 1"


async def _evaluate_active_rule_coverage(
    pool: asyncpg.Pool,
    evaluator: _CoverageEvaluator,
    *,
    sender_key: str,
    source_channel: str,
    evidence_rows: Sequence[asyncpg.Record],
) -> tuple[bool, list[dict[str, str]]]:
    """Evaluate current policy coverage with representative evidence headers.

    This is the same production evaluator used for initial suppression, kept in
    one helper so a pending suggestion is retired by exactly the same matching
    semantics that prevent a new suggestion from being proposed.
    """
    evidence_headers = await _fetch_evidence_headers(pool, evidence_rows)
    coverage_envelope = IngestionEnvelope(
        sender_address=sender_key,
        source_channel=source_channel,
        headers=evidence_headers[0] if evidence_headers else {},
    )
    decision = evaluator.evaluate(coverage_envelope)
    return decision.action != "pass_through", evidence_headers


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def _bump_existing_suggestion(
    pool: asyncpg.Pool,
    pending: asyncpg.Record,
    *,
    sender_key: str,
    source_channel: str,
) -> str:
    """Scenario: 'Repeated evidence bumps an existing pending suggestion'."""
    verdict_action, verdict_target = parse_proposed_action(pending["proposed_action"])
    new_rows = await _fetch_new_agreeing_verdicts(
        pool,
        sender_key=sender_key,
        source_channel=source_channel,
        verdict_action=verdict_action,
        verdict_target=verdict_target,
        after=pending["last_evidence_at"],
    )
    if not new_rows:
        return "skipped_no_new_evidence"
    latest = max(as_utc(r["decided_at"]) for r in new_rows)
    await _bump_suggestion(
        pool,
        suggestion_id=pending["id"],
        evidence_count_delta=len(new_rows),
        last_evidence_at=latest,
    )
    return "suggestions_bumped"


async def _process_candidate(
    pool: asyncpg.Pool,
    evaluator: _CoverageEvaluator,
    *,
    sender_key: str,
    source_channel: str,
    threshold: int,
    min_distinct_days: int,
    min_elapsed: timedelta,
) -> str:
    """Evaluate one (sender_key, source_channel) candidate. Returns an
    outcome key matching a :class:`PromotionTriggerResult` field name."""
    pending = await _fetch_pending_suggestion(
        pool, sender_key=sender_key, source_channel=source_channel
    )
    if pending is not None:
        # A covering rule may have been created since the suggestion was
        # proposed. Recheck before adding more evidence so the approval queue
        # does not keep presenting an already-obsolete action.
        latest_evidence = await _fetch_latest_llm_verdicts(
            pool, sender_key=sender_key, source_channel=source_channel, limit=1
        )
        is_covered, _ = await _evaluate_active_rule_coverage(
            pool,
            evaluator,
            sender_key=sender_key,
            source_channel=source_channel,
            evidence_rows=latest_evidence,
        )
        if is_covered:
            if await _supersede_suggestion(pool, suggestion_id=pending["id"]):
                return "suggestions_superseded"
            return "skipped_race_conflict"
        return await _bump_existing_suggestion(
            pool, pending, sender_key=sender_key, source_channel=source_channel
        )

    # No pending suggestion. Before proposing a fresh one, honor an active
    # owner dismissal: a dismissed suggestion sets a cooldown_until window, and
    # re-proposing this sender/channel before it expires would make the
    # dismissal cosmetic (every scan would mint a new card). Skip until it
    # lapses.
    if (
        await _fetch_active_dismissal_cooldown(
            pool, sender_key=sender_key, source_channel=source_channel
        )
        is not None
    ):
        return "skipped_dismissal_cooldown"

    evidence_rows = await _fetch_latest_llm_verdicts(
        pool, sender_key=sender_key, source_channel=source_channel, limit=threshold
    )
    if len(evidence_rows) < threshold:
        return "skipped_insufficient_evidence"

    agreement = verdicts_agree(evidence_rows)
    if agreement is None:
        return "skipped_no_agreement"
    verdict_action, verdict_target = agreement

    proposed_action = build_proposed_action(verdict_action, verdict_target)
    if proposed_action is None:
        return "skipped_no_rule_equivalent"

    timestamps = [r["decided_at"] for r in evidence_rows]
    if not passes_evidence_quality_gate(
        timestamps, min_distinct_days=min_distinct_days, min_elapsed=min_elapsed
    ):
        return "skipped_insufficient_evidence"

    # "No enabled ingestion_rules row already covers it" — reuse the real
    # production evaluator (scope='global') so this check stays in lockstep
    # with actual routing behavior instead of a hand-rolled duplicate of the
    # rule-matching logic. The most recent evidence event's headers are used
    # as a representative sample for header_condition rule coverage checks.
    is_covered, evidence_headers = await _evaluate_active_rule_coverage(
        pool,
        evaluator,
        sender_key=sender_key,
        source_channel=source_channel,
        evidence_rows=evidence_rows,
    )
    if is_covered:
        return "skipped_existing_rule"

    is_automated = compute_is_clearly_automated(sender_key, evidence_headers)
    utc_timestamps = [as_utc(ts) for ts in timestamps]

    created_id = await _insert_suggestion(
        pool,
        sender_key=sender_key,
        source_channel=source_channel,
        proposed_condition={"address": sender_key},
        proposed_action=proposed_action,
        evidence_count=len(evidence_rows),
        first_evidence_at=min(utc_timestamps),
        last_evidence_at=max(utc_timestamps),
        is_clearly_automated=is_automated,
    )
    if created_id is None:
        return "skipped_race_conflict"
    return "suggestions_created"


async def run_rule_promotion_trigger(
    pool: asyncpg.Pool,
    *,
    threshold: int = DEFAULT_PROMOTION_THRESHOLD,
    min_distinct_days: int = DEFAULT_MIN_DISTINCT_DAYS,
    min_elapsed: timedelta = DEFAULT_MIN_ELAPSED_FLOOR,
    lookback: timedelta = DEFAULT_LOOKBACK_WINDOW,
    evaluator: _CoverageEvaluator | None = None,
) -> dict[str, int]:
    """Scan ``routing_verdict_log`` and propose (or bump) rule promotions.

    See the module docstring for the evidence-quality gate, automated-sender
    classification, and bounded-scan design decisions. Never raises for a
    single candidate's failure — errors are counted and logged, so one bad
    row cannot abort the whole scan.
    """
    result = PromotionTriggerResult()

    if evaluator is None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=pool, refresh_interval_s=3600)
    await evaluator.ensure_loaded()

    since = datetime.now(UTC) - lookback
    candidates = await _fetch_candidates(pool, since=since)
    result.candidates_scanned = len(candidates)

    for row in candidates:
        sender_key = row["sender_key"]
        source_channel = row["source_channel"]
        if not sender_key or not source_channel:
            continue
        try:
            outcome = await _process_candidate(
                pool,
                evaluator,
                sender_key=sender_key,
                source_channel=source_channel,
                threshold=threshold,
                min_distinct_days=min_distinct_days,
                min_elapsed=min_elapsed,
            )
        except Exception:
            logger.exception(
                "rule_promotion_trigger: error processing candidate "
                "sender_key=%s source_channel=%s",
                sender_key,
                source_channel,
            )
            result.errors += 1
            continue

        current = getattr(result, outcome, None)
        if current is None:
            logger.warning("rule_promotion_trigger: unknown outcome %r", outcome)
            continue
        setattr(result, outcome, current + 1)

    return result.as_dict()
