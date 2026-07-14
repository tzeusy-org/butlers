// ---------------------------------------------------------------------------
// DecisionsVerdictOpener -- /decisions page opener (bu-ckkpz.2, epic bu-ckkpz
// "Owner Decision Desk")
//
// Composes the open-decisions digest GET /api/decisions already fetches into
// one synthesized verdict line via the shared DispatchVerdict primitive,
// matching the epic's own vocabulary for the backend's weekly digest message
// (butlers.jobs.decision_review._compose_weekly_digest_message):
// "N decisions waiting, oldest Xd". Mirrors ApprovalsVerdictOpener's
// composition pattern (components/approvals/approvals-verdict-opener.tsx).
// ---------------------------------------------------------------------------

import type { DecisionBeadSummary } from "@/api/index.ts";
import { DispatchVerdict, type VerdictClause } from "@/components/ui/dispatch-verdict";

/** Same coarse age vocabulary as the backend's own `_format_age` (decision_review.py). */
function formatAgeHours(ageHours: number): string {
  if (ageHours >= 24) {
    const days = Math.floor(ageHours / 24);
    return `${days}d`;
  }
  const hours = Math.max(Math.floor(ageHours), 0);
  return `${hours}h`;
}

function buildClauses(
  decisions: DecisionBeadSummary[],
  decisionsAvailable: boolean | undefined,
): VerdictClause[] {
  const clauses: VerdictClause[] = [];

  // Never fabricate an all-clear (CLAUDE.md degraded-envelope convention):
  // `decisions_available === false` means the beads-export digest could not
  // be read -- an empty `decisions` list here does NOT mean "nothing
  // waiting", so this clause alone suppresses the calm all-clear line.
  if (decisionsAvailable === false) {
    clauses.push({
      key: "decisions-unavailable",
      text: "decision digest unavailable: beads export unreachable",
    });
    return clauses;
  }

  if (decisions.length > 0) {
    // `decisions` is already oldest-first (backend sorts by created_at asc).
    const oldest = decisions[0];
    const count = decisions.length;
    clauses.push({
      key: "waiting",
      text: `${count} decision${count !== 1 ? "s" : ""} waiting, oldest ${formatAgeHours(oldest.age_hours)}`,
    });

    const escalatedCount = decisions.filter((d) => d.escalated).length;
    if (escalatedCount > 0) {
      clauses.push({
        key: "escalated",
        text:
          escalatedCount === 1
            ? "1 blocking a P1 bug or deploy"
            : `${escalatedCount} blocking a P1 bug or deploy`,
      });
    }
  }

  return clauses;
}

export function DecisionsVerdictOpener({
  decisions,
  isLoading,
  isError,
  decisionsAvailable,
}: {
  decisions: DecisionBeadSummary[];
  isLoading: boolean;
  isError: boolean;
  /** From `meta.decisions_available` -- undefined while loading/erroring. */
  decisionsAvailable?: boolean;
}) {
  const clauses = buildClauses(decisions, decisionsAvailable);

  return (
    <DispatchVerdict
      testId="decisions"
      landmarkLabel="Decisions verdict"
      sources={[{ label: "decision digest", isLoading, isError }]}
      clauses={clauses}
      allClear="No decisions waiting."
    />
  );
}
