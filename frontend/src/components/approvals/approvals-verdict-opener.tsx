// ---------------------------------------------------------------------------
// ApprovalsVerdictOpener -- /approvals page opener (bu-qvnce.9, JARVIS
// pursuit move 9, slice 2)
//
// Composes the pending queue + decided-history data ApprovalsPage already
// fetches into one synthesized verdict line via the shared DispatchVerdict
// primitive: "3 waiting; nearest expires in 40m; one stalled action never
// ran" -- each clause a real door into the exact row it describes. ("stalled"
// not "approved": this page never renders the raw "approved" status text
// anywhere -- see ApprovalsPage's statusLabel doctrine comment.)
// ---------------------------------------------------------------------------

import type { ApprovalSummary } from "@/api/index.ts";
import { DispatchVerdict, type VerdictClause } from "@/components/ui/dispatch-verdict";

const HOUR_MS = 3_600_000;

/** Same countdown vocabulary as ApprovalsPage's own expiryCountdown (kept in sync manually -- both read expires_at the same way). */
function countdownText(expiresAt: string | null | undefined): string | null {
  if (!expiresAt) return null;
  const expiresDate = new Date(expiresAt);
  if (Number.isNaN(expiresDate.getTime())) return null;
  const msLeft = expiresDate.getTime() - Date.now();
  if (msLeft <= 0) return "expired";
  const mins = Math.round(msLeft / 60_000);
  if (mins < 60) return `expires in ${mins}m`;
  const hours = Math.round(msLeft / HOUR_MS);
  if (hours < 24) return `expires in ${hours}h`;
  const days = Math.round(msLeft / (24 * HOUR_MS));
  return `expires in ${days}d`;
}

function buildClauses(
  pending: ApprovalSummary[],
  history: ApprovalSummary[],
  sourcesDegraded: string[],
): VerdictClause[] {
  const clauses: VerdictClause[] = [];

  // Degraded fan-out (bu-jad4j.4): the backend fans the queue + history across
  // each butler's pool and drops any that error, naming them in
  // `meta.sources_degraded` rather than failing the request (approvals.py's
  // DegradedSources). When that list is non-empty the counts below undercount,
  // so the calm "No approvals waiting." verdict is a half-truth. Prepend a
  // named clause (source-health first, mirroring DispatchVerdict's own
  // error-clause ordering) — its mere presence suppresses the all-clear line
  // so a downed pool never renders as a clear queue (CLAUDE.md degraded-
  // envelope convention).
  if (sourcesDegraded.length > 0) {
    clauses.push({
      key: "sources-degraded",
      text: `${sourcesDegraded.join(", ")} unreachable: some approvals may be missing`,
    });
  }

  if (pending.length > 0) {
    clauses.push({ key: "waiting", text: `${pending.length} waiting`, href: "/approvals" });

    const withExpiry = pending
      .filter((a) => a.expires_at)
      .sort((a, b) => new Date(a.expires_at!).getTime() - new Date(b.expires_at!).getTime());
    const nearest = withExpiry[0];
    const text = nearest ? countdownText(nearest.expires_at) : null;
    if (nearest && text) {
      clauses.push({ key: "nearest-expiry", text: `nearest ${text}`, href: `/approvals/${nearest.id}` });
    }
  }

  // The backend's raw "approved" status always means approved-but-never-
  // dispatched (see ApprovalsPage's statusColor/statusLabel doctrine
  // comment) -- rendered everywhere on this page as "stalled", never the
  // literal word "approved" (JARVIS audit move 9 -- never success-green),
  // so the clause below follows the same vocabulary.
  const stalled = history.filter((item) => item.status === "approved");
  if (stalled.length === 1) {
    clauses.push({
      key: "stalled",
      text: "one stalled action never ran",
      href: `/approvals/${stalled[0].id}`,
    });
  } else if (stalled.length > 1) {
    clauses.push({
      key: "stalled",
      text: `${stalled.length} stalled actions never ran`,
      href: "/approvals",
    });
  }

  return clauses;
}

export function ApprovalsVerdictOpener({
  pending,
  pendingLoading,
  pendingError,
  pendingSourcesDegraded = [],
  history,
  historyLoading,
  historyError,
  historySourcesDegraded = [],
}: {
  pending: ApprovalSummary[];
  pendingLoading: boolean;
  pendingError: boolean;
  /** Butler pools dropped from the queue fan-out (queue `meta.sources_degraded`). */
  pendingSourcesDegraded?: string[];
  history: ApprovalSummary[];
  historyLoading: boolean;
  historyError: boolean;
  /** Butler pools dropped from the history fan-out (history `meta.sources_degraded`). */
  historySourcesDegraded?: string[];
}) {
  // A pool that dropped from either fan-out means this whole verdict may be
  // incomplete; dedupe the two lists into one named clause (a butler down for
  // the queue is almost always down for history too).
  const sourcesDegraded = [
    ...new Set([...pendingSourcesDegraded, ...historySourcesDegraded]),
  ];
  const clauses = buildClauses(pending, history, sourcesDegraded);

  return (
    <DispatchVerdict
      testId="approvals"
      landmarkLabel="Approvals verdict"
      sources={[
        { label: "approvals queue", isLoading: pendingLoading, isError: pendingError },
        { label: "approval history", isLoading: historyLoading, isError: historyError },
      ]}
      clauses={clauses}
      allClear="No approvals waiting."
    />
  );
}
