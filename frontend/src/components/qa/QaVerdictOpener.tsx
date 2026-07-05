// ---------------------------------------------------------------------------
// QaVerdictOpener -- QA staffer masthead opener for /qa (bu-qvnce.9, JARVIS
// pursuit move 9, slice 2)
//
// GET /api/qa/summary already returns staffer_status / last_patrol_at /
// next_patrol_at / circuit_breaker / credentials_status (types.ts:5048-5071)
// but QaOverviewPage never rendered them (they were fetched only to feed the
// KPI strip). This composes them into the page opener via the shared
// DispatchVerdict primitive instead of leaving them on the wire unused.
// ---------------------------------------------------------------------------

import type { QaSummary } from "@/api/types";
import { DispatchVerdict, type VerdictClause } from "@/components/ui/dispatch-verdict";
import { formatRelativeCompact } from "@/components/ui/time";
import type { useQaSummary } from "@/hooks/use-qa";

// staffer_status values per src/butlers/api/routers/qa.py:1527-1535: only
// "circuit_breaker_tripped", "error", and "unknown" name a real problem --
// "healthy" and any future/unrecognized value fall through as fine
// (forward-compatible with new backend values instead of misreading them as
// a problem).
function buildClauses(summary: QaSummary): VerdictClause[] {
  const clauses: VerdictClause[] = [];

  if (summary.circuit_breaker.tripped) {
    clauses.push({
      key: "breaker-tripped",
      text: `circuit breaker tripped after ${summary.circuit_breaker.consecutive_failures} consecutive failures`,
    });
  } else if (summary.staffer_status === "error") {
    clauses.push({
      key: "last-patrol-failed",
      text: "last patrol failed",
      href: summary.last_patrol ? `/qa/patrols/${summary.last_patrol.id}` : undefined,
    });
  } else if (summary.staffer_status === "unknown") {
    clauses.push({ key: "no-patrol-history", text: "no patrol history yet" });
  }

  const creds = summary.credentials_status;
  if (creds.gh_token_present === false) {
    clauses.push({
      key: "gh-token-missing",
      text: creds.provisioning_hint ?? "GitHub token missing",
    });
  }
  if (creds.git_author_name_present === false || creds.git_author_email_present === false) {
    clauses.push({ key: "git-author-missing", text: "git author identity missing" });
  }

  return clauses;
}

function buildAllClear(summary: QaSummary): string {
  const parts = [
    summary.last_patrol_at ? `last patrol ${formatRelativeCompact(new Date(summary.last_patrol_at))}` : null,
    summary.next_patrol_at ? `next patrol ${formatRelativeCompact(new Date(summary.next_patrol_at))}` : null,
  ].filter((p): p is string => Boolean(p));

  return parts.length > 0 ? `QA staffer healthy: ${parts.join(", ")}` : "QA staffer healthy";
}

export function QaVerdictOpener({ summary }: { summary: ReturnType<typeof useQaSummary> }) {
  const data = summary.data?.data;
  const clauses = data ? buildClauses(data) : [];

  return (
    <DispatchVerdict
      testId="qa"
      landmarkLabel="QA verdict"
      sources={[{ label: "QA summary", isLoading: summary.isLoading, isError: summary.isError }]}
      clauses={clauses}
      allClear={data ? buildAllClear(data) : "QA staffer healthy"}
    />
  );
}
