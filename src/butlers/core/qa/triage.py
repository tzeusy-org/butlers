"""QA triage engine — source-agnostic deduplication and prioritization.

Accepts ``QaFinding`` objects from any discovery source and applies a
three-source deduplication check before determining which findings are novel
and warrant investigation dispatch.

Deduplication sources (checked in order):
1. Active healing attempts (status: dispatch_pending, investigating, pr_open)
2. Local dismissal cache (public.qa_dismissals, active rows only)
3. Per-fingerprint cooldown (recent terminal attempts within cooldown window)

Novel findings are then sorted by severity (ascending — critical=0 first) and
occurrence_count (descending — most frequent first).

Spec reference
--------------
openspec/changes/qa-staffer/specs/qa-triage/spec.md
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

import asyncpg

from butlers.core.healing.tracking import get_active_attempt, get_recent_attempt
from butlers.core.qa.dismissals import is_dismissed
from butlers.core.qa.findings import insert_finding
from butlers.core.qa.models import QaFinding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default cooldown window in minutes — matches HealingConfig default.
DEFAULT_COOLDOWN_MINUTES = 60


# ---------------------------------------------------------------------------
# Triage result types
# ---------------------------------------------------------------------------


@dataclass
class TriagedFinding:
    """A finding that has passed through triage processing.

    Attributes
    ----------
    finding:
        The original normalized QaFinding.
    dedup_reason:
        ``None`` for novel findings; otherwise the deduplication reason:
        ``"active_investigation"``, ``"dismissed"``, or ``"cooldown"``.
    finding_id:
        UUID of the inserted ``public.qa_findings`` row.
    linked_attempt_id:
        UUID of the existing healing_attempts row if the finding was deduplicated
        against an active investigation, or ``None``.
    is_novel:
        ``True`` when ``dedup_reason is None``.
    """

    finding: QaFinding
    dedup_reason: str | None
    finding_id: uuid.UUID
    linked_attempt_id: uuid.UUID | None = None

    @property
    def is_novel(self) -> bool:
        """Return ``True`` when this finding is novel (not deduplicated)."""
        return self.dedup_reason is None


@dataclass
class TriageResult:
    """Result of a single triage cycle.

    Attributes
    ----------
    all_findings:
        All ``TriagedFinding`` objects, including deduplicated ones.
        Used for ``qa_findings`` persistence and dashboard reporting.
    novel_findings:
        Subset of ``all_findings`` with ``is_novel == True``, sorted by
        priority (severity asc, occurrence_count desc).
    dedup_counts:
        Mapping of dedup_reason → count.  Novel findings have reason ``None``
        (counted under key ``None``).
    """

    all_findings: list[TriagedFinding] = field(default_factory=list)
    novel_findings: list[TriagedFinding] = field(default_factory=list)
    dedup_counts: dict[str | None, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Core triage logic
# ---------------------------------------------------------------------------


async def triage_findings(
    pool: asyncpg.Pool,
    patrol_id: uuid.UUID,
    findings: list[QaFinding],
    cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
) -> TriageResult:
    """Apply deduplication and prioritization to a batch of raw findings.

    All findings (novel and deduplicated) are persisted in ``public.qa_findings``
    linked to *patrol_id*.  Novel findings are returned in priority order.

    Deduplication is performed source-agnostically: all findings from
    log_scanner, session_records, butler_reports, and future sources go through
    the same gate sequence.

    Cross-source dedup: multiple findings with the same fingerprint from
    different sources within one patrol cycle are coalesced — only the first
    occurrence is processed as potential-novel; subsequent ones with the same
    fingerprint are marked ``"active_investigation"`` (they are deduplicated
    against the row inserted by the first occurrence).

    Note: triage performs a fast, non-atomic dedup check to filter obvious
    duplicates early.  The dispatch layer performs the authoritative atomic
    claim (novelty gate via INSERT ON CONFLICT in create_or_join_attempt).
    Cooldown appears in both layers intentionally — triage's check is a
    fast-path optimization; dispatch's is the atomic guarantee.

    Parameters
    ----------
    pool:
        asyncpg connection pool targeting the public schema.
    patrol_id:
        UUID of the current qa_patrols row (all findings link to this patrol).
    findings:
        Raw findings from one or more discovery sources.  May contain
        duplicates (same fingerprint from different sources or repeated
        discovery within one scan window).
    cooldown_minutes:
        How far back (in minutes) to check for recent terminal attempts.

    Returns
    -------
    TriageResult
        Contains all triaged findings (with DB IDs), novel subset (sorted),
        and dedup count breakdown.
    """
    result = TriageResult()

    # Track fingerprints seen in this patrol cycle to handle cross-source dedup.
    seen_fingerprints: dict[str, uuid.UUID] = {}  # fingerprint → first finding_id

    for finding in findings:
        fp = finding.fingerprint
        dedup_reason: str | None = None
        linked_attempt_id: uuid.UUID | None = None

        # ------------------------------------------------------------------
        # Intra-patrol dedup: same fingerprint already seen this cycle
        # ------------------------------------------------------------------
        if fp in seen_fingerprints:
            dedup_reason = "active_investigation"
            logger.debug(
                "Triage: intra-patrol dedup fingerprint=%s source=%s butler=%s",
                fp[:12],
                finding.source_type,
                finding.source_butler,
            )
            finding_id = await insert_finding(pool, patrol_id, finding, dedup_reason, None)
            triaged = TriagedFinding(
                finding=finding,
                dedup_reason=dedup_reason,
                finding_id=finding_id,
                linked_attempt_id=None,
            )
            result.all_findings.append(triaged)
            result.dedup_counts[dedup_reason] = result.dedup_counts.get(dedup_reason, 0) + 1
            continue

        # ------------------------------------------------------------------
        # Gate 1: Check active healing attempts
        # ------------------------------------------------------------------
        if dedup_reason is None:
            active_attempt = await get_active_attempt(pool, fp)
            if active_attempt is not None:
                dedup_reason = "active_investigation"
                linked_attempt_id = active_attempt["id"]
                logger.debug(
                    "Triage: dedup fingerprint=%s reason=active_investigation attempt=%s",
                    fp[:12],
                    linked_attempt_id,
                )

        # ------------------------------------------------------------------
        # Gate 2: Check dismissal cache
        # ------------------------------------------------------------------
        if dedup_reason is None:
            dismissed = await is_dismissed(pool, fp)
            if dismissed:
                dedup_reason = "dismissed"
                logger.debug(
                    "Triage: dedup fingerprint=%s reason=dismissed",
                    fp[:12],
                )

        # ------------------------------------------------------------------
        # Gate 3: Cooldown check
        # ------------------------------------------------------------------
        if dedup_reason is None:
            recent = await get_recent_attempt(pool, fp, cooldown_minutes)
            if recent is not None:
                dedup_reason = "cooldown"
                logger.debug(
                    "Triage: dedup fingerprint=%s reason=cooldown closed_at=%s",
                    fp[:12],
                    recent.get("closed_at"),
                )

        # ------------------------------------------------------------------
        # Persist the finding
        # ------------------------------------------------------------------
        # Pass linked_attempt_id so that findings deduplicated against an
        # active investigation record the existing attempt FK immediately.
        # For novel findings (dedup_reason is None) this is also None; the
        # dispatcher updates it after create_or_join_attempt succeeds.
        finding_id = await insert_finding(pool, patrol_id, finding, dedup_reason, linked_attempt_id)

        triaged = TriagedFinding(
            finding=finding,
            dedup_reason=dedup_reason,
            finding_id=finding_id,
            linked_attempt_id=linked_attempt_id,
        )
        result.all_findings.append(triaged)
        result.dedup_counts[dedup_reason] = result.dedup_counts.get(dedup_reason, 0) + 1

        if dedup_reason is None:
            # Novel — track fingerprint so intra-patrol duplicates are caught
            seen_fingerprints[fp] = finding_id
            result.novel_findings.append(triaged)

    # ------------------------------------------------------------------
    # Sort novel findings: severity asc (critical=0 first), then
    # occurrence_count desc (most frequent first).
    # ------------------------------------------------------------------
    result.novel_findings.sort(key=lambda tf: (tf.finding.severity, -tf.finding.occurrence_count))

    if result.novel_findings:
        logger.info(
            "Triage complete: patrol=%s total=%d novel=%d dedup_counts=%s",
            patrol_id,
            len(result.all_findings),
            len(result.novel_findings),
            {str(k): v for k, v in result.dedup_counts.items()},
        )

    return result
