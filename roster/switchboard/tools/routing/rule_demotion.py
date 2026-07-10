"""Demotion via spot-check sampling — rule-promotion bead 5 of 7 (bu-x55k3).

Per ``docs/plans/2026-07-06-switchboard-rule-promotion-design.md`` section 4
and the merged openspec change ``switchboard-rule-promotion``
(``openspec/changes/switchboard-rule-promotion/specs/switchboard-rule-promotion/spec.md``,
"Requirement: Demotion via Spot-Check Sampling"). Beads 1-4 built the
mining substrate (``routing_verdict_log``, sw_019), the suggestion schema
with ``suggestion_kind`` (``rule_promotion_suggestions``, sw_020), and the
promotion trigger job. This bead closes the loop the other direction:
detecting when a *promoted* rule has started drifting from what a fresh LLM
classification would decide, and proposing its revocation.

How a spot-check happens (see ``butlers.ingestion_policy.PolicyDecision.spot_check``
and ``src/butlers/modules/pipeline.py``'s triage-bypass guard): when
``IngestionPolicyEvaluator.evaluate()`` matches a rule with
``created_by='promotion'``, it rolls a 1-in-K die (default K=20, injectable
sampler for deterministic tests). On a hit, the matching pipeline code path
does *not* take the rule's bypass — the event falls through to normal LLM
classification exactly as an unmatched (``pass_through``) event would, and
the resulting fresh verdict is recorded in ``routing_verdict_log`` with
``verdict_source='spot_check'`` and ``matched_rule_id`` set to the sampled
rule. This module is what runs *after* that write: it re-reads the sampled
rule's own current action, compares it against the rolling window of recent
spot-check verdicts for that rule, and — on sustained disagreement — files a
``rule_promotion_suggestions`` row with ``suggestion_kind='demotion'``.

No new storage beyond one index (migration sw_021): the rolling agreement
score is computed on demand from ``routing_verdict_log`` rows already
written by the spot-check hook, rather than maintained as separate mutable
state. This keeps storage minimal (per the dispatch's "prefer minimal
storage" instruction) and makes the score trivially re-derivable/auditable
from the existing ledger.

Honesty doctrine (spot-checks that never resolve are neutral, not evidence):
a spot-check that fails to run — the classification spawn errors, times out,
or otherwise never reaches a definitive tool-call resolution — writes no
``routing_verdict_log`` row at all (mirrors the pre-existing behavior for an
ordinary LLM classification: ``record_routing_verdict`` is only ever called
once ``_extract_routed_butlers()`` resolves at least one target). Since this
module's agreement window is built entirely from rows that *did* get
written, a did-not-run spot-check is structurally excluded from the
window — it can neither count as agreement nor disagreement. This satisfies
the "ran-and-agreed / ran-and-disagreed / did-not-run" three-way distinction
without any extra bookkeeping.

Spot-check counterpart verdicts for skip/metadata_only rules (bu-wa3nb): the
ordinary ``verdict_source='llm'`` mining path (bead 1, sw_019) only ever
writes rows for the "LLM called route_to_butler" outcome — the no-tool-call
fallback is "deliberately NOT logged... would pollute promotion mining with
noise". That asymmetry would once have biased *demotion* for the other rule
shapes: a spot-checked ``skip``/``metadata_only`` promoted rule whose fresh
classification *agrees* (the LLM effectively does nothing) resolves to no
route target, so the route_to write loop records no counterpart row, while a
spot-checked ``skip`` rule where the LLM *disagrees* and *does* call
``route_to_butler`` always produces one — agreement for those rule types
would be under-counted relative to disagreement. ``pipeline.py`` now closes
this specifically for the ``verdict_source='spot_check'`` path: when a
suppressed-bypass spot-check of a ``skip``/``metadata_only`` rule produces no
route (the classification ran and the LLM agreed the message should not be
routed), it records a ``spot_check`` row carrying the rule's own suppressed
decision (``verdict_action='skip'``/``'metadata_only'``, ``verdict_target``
None), so :func:`compute_agreement` counts it as an agreement sample instead
of the window seeing disagreements only. A route_to spot-check that produced
no route stays unlogged (genuinely undeterminable, not an agreement), and a
did-not-run spot-check (spawn error/timeout) still writes nothing, preserving
the honesty doctrine above. This only completes the *demotion* (spot_check)
ledger; the general ``verdict_source='llm'`` promotion-mining fallback is
untouched, and bead 3's trigger still mines only ``route_to`` suggestions —
so no ``skip``/``metadata_only`` rule is promoted or demoted yet, but the
spot-check agreement data is now complete for the day one is (that promotion
decision remains bu-4pq0s's, out of this fix's scope).

[decision] Minimum sample floor before evaluating demotion: the spec says
"a rolling per-rule agreement score over the most recent 20 spot-checks" but
does not pin down a minimum sample count before that score is *actionable*.
Evaluating on a single spot-check (a rule with exactly one recorded
disagreement would show a 0% score) would demote on noise. This module
requires at least ``DEFAULT_MIN_SAMPLES`` (5) recorded spot-checks in the
window before comparing against the threshold — small enough to react
quickly (a K=20 sampler needs ~100 matching events to accumulate 5 samples),
large enough that one bad spot-check cannot single-handedly demote a rule.
Reversible, flagged rather than silently picked, consistent with how bead 3
flagged its own evidence-quality-gate parameters.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

DEFAULT_AGREEMENT_WINDOW = 20
"""Rolling window size: the most recent N spot-checks per rule (design.md
section 4 / spec default)."""

DEFAULT_AGREEMENT_THRESHOLD = 0.90
"""Below this fraction, a demotion suggestion is filed (spec default: 90%)."""

DEFAULT_MIN_SAMPLES = 5
"""[decision] Minimum recorded spot-checks in the window before the
agreement score is actionable — see module docstring."""

_ROUTE_TO_PREFIX = "route_to:"


def parse_rule_action(action: str) -> tuple[str, str | None]:
    """Parse an ``ingestion_rules.action`` string into ``(action, target)``.

    Mirrors ``butlers.ingestion_policy._parse_action`` (duplicated rather
    than imported — that function is a private module internal; this is a
    two-line inverse mapping, same duplication rationale as
    ``rule_promotion.py``'s ``build_proposed_action``/``parse_proposed_action``).
    """
    if action.startswith(_ROUTE_TO_PREFIX):
        target = action[len(_ROUTE_TO_PREFIX) :]
        return "route_to", target or None
    return action, None


@dataclass(frozen=True)
class AgreementResult:
    """Rolling agreement score over a rule's recent spot-check verdicts."""

    sample_count: int
    agreement_count: int

    @property
    def agreement_score(self) -> float | None:
        """Fraction of samples that agreed with the rule, or ``None`` if
        there were no samples (unscoreable, not 0% or 100%)."""
        if self.sample_count == 0:
            return None
        return self.agreement_count / self.sample_count


def compute_agreement(
    rows: Sequence[Mapping[str, Any]],
    *,
    rule_action: str,
    rule_target: str | None,
) -> AgreementResult:
    """Compare each spot-check row's verdict against the rule's own action.

    A row "agrees" when its ``(verdict_action, verdict_target)`` matches the
    rule's own ``(rule_action, rule_target)``. Pure/no I/O — unit-tested
    directly against synthetic rows.
    """
    agreement_count = 0
    for row in rows:
        if row["verdict_action"] == rule_action and row["verdict_target"] == rule_target:
            agreement_count += 1
    return AgreementResult(sample_count=len(rows), agreement_count=agreement_count)


@dataclass(frozen=True)
class DemotionCheckResult:
    """Outcome of one :func:`maybe_create_demotion_suggestion` call."""

    ran: bool
    """False only when the call could not evaluate at all (missing pool,
    rule not found/not promoted, or an error — degraded-honesty, never
    raises)."""

    demoted_suggestion_created: bool
    agreement_score: float | None
    sample_count: int
    reason: str = ""


async def _fetch_promoted_rule(pool: asyncpg.Pool, rule_id: str) -> asyncpg.Record | None:
    """Fetch the rule's own current action — authoritative, not cached.

    Only returns a row for rules that are still ``created_by='promotion'``;
    a rule hand-edited back to a dashboard-authored one (or already
    demoted/disabled) is not a demotion candidate.
    """
    return await pool.fetchrow(
        """
        SELECT id, action, created_by, enabled
        FROM ingestion_rules
        WHERE id = $1
        """,
        rule_id,
    )


async def _fetch_pending_demotion(pool: asyncpg.Pool, rule_id: str) -> asyncpg.Record | None:
    """The at-most-one pending demotion suggestion already tracking this rule."""
    return await pool.fetchrow(
        """
        SELECT id
        FROM rule_promotion_suggestions
        WHERE target_rule_id = $1
          AND status = 'pending_review' AND suggestion_kind = 'demotion'
        """,
        rule_id,
    )


async def _fetch_recent_spot_checks(
    pool: asyncpg.Pool, rule_id: str, limit: int
) -> list[asyncpg.Record]:
    """The most recent *limit* spot-check verdicts recorded for this rule.

    Uses the ``(matched_rule_id, decided_at DESC) WHERE verdict_source =
    'spot_check'`` partial index (migration sw_021) — bounded, not a table
    scan.
    """
    return await pool.fetch(
        """
        SELECT verdict_action, verdict_target, decided_at
        FROM routing_verdict_log
        WHERE matched_rule_id = $1 AND verdict_source = 'spot_check'
        ORDER BY decided_at DESC
        LIMIT $2
        """,
        rule_id,
        limit,
    )


async def _insert_demotion_suggestion(
    pool: asyncpg.Pool,
    *,
    rule_id: str,
    evidence_count: int,
    first_evidence_at: datetime,
    last_evidence_at: datetime,
) -> Any:
    """Insert a pending demotion suggestion for *rule_id*.

    ``ON CONFLICT ... DO NOTHING`` against the unique partial index
    (``ux_rule_promotion_suggestions_pending_demotion``, sw_020) so a race
    against a concurrently-created demotion suggestion for the same rule is
    a silent no-op, matching ``rule_promotion.py``'s ``_insert_suggestion``
    race-handling convention for promotions.
    """
    return await pool.fetchval(
        """
        INSERT INTO rule_promotion_suggestions
            (suggestion_kind, target_rule_id, evidence_count,
             first_evidence_at, last_evidence_at, status)
        VALUES ('demotion', $1, $2, $3, $4, 'pending_review')
        ON CONFLICT (target_rule_id)
            WHERE status = 'pending_review' AND suggestion_kind = 'demotion'
        DO NOTHING
        RETURNING id
        """,
        rule_id,
        evidence_count,
        first_evidence_at,
        last_evidence_at,
    )


async def maybe_create_demotion_suggestion(
    pool: asyncpg.Pool | None,
    *,
    rule_id: str,
    window: int = DEFAULT_AGREEMENT_WINDOW,
    threshold: float = DEFAULT_AGREEMENT_THRESHOLD,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> DemotionCheckResult:
    """Re-score a promoted rule's rolling spot-check agreement; file a
    demotion suggestion on sustained disagreement.

    Best-effort — mirrors ``record_routing_verdict``'s degraded-honesty
    contract (never raises, never blocks the routing decision it follows).
    Called once per spot-check, right after the fresh verdict is recorded
    (see ``pipeline.py``), so the check itself is cheap: one row-by-id fetch,
    one ``LIMIT``-bounded indexed query, one conditional insert.

    Demotion is *never* auto-applied here — this only ever creates a
    ``pending_review`` suggestion; disabling the rule requires the same
    owner-confirmed action as promotion (spec "Requirement: Demotion via
    Spot-Check Sampling", "Scenario: Rule is never auto-disabled").
    """
    if pool is None:
        return DemotionCheckResult(
            ran=False,
            demoted_suggestion_created=False,
            agreement_score=None,
            sample_count=0,
            reason="no_pool",
        )

    try:
        rule = await _fetch_promoted_rule(pool, rule_id)
        if rule is None:
            return DemotionCheckResult(
                ran=False,
                demoted_suggestion_created=False,
                agreement_score=None,
                sample_count=0,
                reason="rule_not_found",
            )
        if rule["created_by"] != "promotion":
            return DemotionCheckResult(
                ran=False,
                demoted_suggestion_created=False,
                agreement_score=None,
                sample_count=0,
                reason="not_a_promoted_rule",
            )
        if not rule["enabled"]:
            return DemotionCheckResult(
                ran=False,
                demoted_suggestion_created=False,
                agreement_score=None,
                sample_count=0,
                reason="rule_disabled",
            )

        pending = await _fetch_pending_demotion(pool, rule_id)
        if pending is not None:
            return DemotionCheckResult(
                ran=True,
                demoted_suggestion_created=False,
                agreement_score=None,
                sample_count=0,
                reason="pending_demotion_exists",
            )

        rule_action, rule_target = parse_rule_action(rule["action"])
        rows = await _fetch_recent_spot_checks(pool, rule_id, window)
        result = compute_agreement(rows, rule_action=rule_action, rule_target=rule_target)

        if result.sample_count < min_samples:
            return DemotionCheckResult(
                ran=True,
                demoted_suggestion_created=False,
                agreement_score=result.agreement_score,
                sample_count=result.sample_count,
                reason="insufficient_samples",
            )

        assert result.agreement_score is not None  # sample_count >= min_samples > 0
        if result.agreement_score >= threshold:
            return DemotionCheckResult(
                ran=True,
                demoted_suggestion_created=False,
                agreement_score=result.agreement_score,
                sample_count=result.sample_count,
                reason="agreement_above_threshold",
            )

        timestamps = [r["decided_at"] for r in rows]
        created_id = await _insert_demotion_suggestion(
            pool,
            rule_id=rule_id,
            evidence_count=result.sample_count,
            first_evidence_at=min(timestamps),
            last_evidence_at=max(timestamps),
        )
        return DemotionCheckResult(
            ran=True,
            demoted_suggestion_created=created_id is not None,
            agreement_score=result.agreement_score,
            sample_count=result.sample_count,
            reason="demotion_created" if created_id is not None else "race_conflict",
        )
    except Exception:
        logger.warning(
            "maybe_create_demotion_suggestion: failed to evaluate rule_id=%s "
            "(degraded-honesty: never blocks routing)",
            rule_id,
            exc_info=True,
        )
        return DemotionCheckResult(
            ran=False,
            demoted_suggestion_created=False,
            agreement_score=None,
            sample_count=0,
            reason="error",
        )
