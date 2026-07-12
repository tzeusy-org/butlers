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

// Mirrors qa.py's _CIRCUIT_BREAKER_THRESHOLD default (5), used only as a
// fallback for fixtures/callers that predate the wire-level threshold field.
const DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 5;

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
  } else {
    // A pre-trip streak is worth naming BEFORE the breaker actually opens --
    // "the breaker is about to trip" is itself an early-warning verdict, not
    // just a post-mortem after it already has (bu-hmdqz.9).
    if (summary.circuit_breaker.consecutive_failures > 0) {
      const n = summary.circuit_breaker.consecutive_failures;
      const threshold = summary.circuit_breaker.threshold ?? DEFAULT_CIRCUIT_BREAKER_THRESHOLD;
      clauses.push({
        key: "pre-trip-failure-streak",
        text: `${n} consecutive failure${n === 1 ? "" : "s"} — breaker opens at ${threshold}`,
      });
    }
    if (summary.staffer_status === "error") {
      clauses.push({
        key: "last-patrol-failed",
        text: "last patrol failed",
        href: summary.last_patrol ? `/qa/patrols/${summary.last_patrol.id}` : undefined,
      });
    } else if (summary.staffer_status === "unknown") {
      clauses.push({ key: "no-patrol-history", text: "no patrol history yet" });
    }
  }

  const overdueClause = buildOverduePatrolClause(summary);
  if (overdueClause) clauses.push(overdueClause);

  // The watcher's own runtime-CLI credential health -- staffer_status can
  // read 'healthy' while the QA staffer's own model dispatch is dying on a
  // revoked token (bu-hmdqz.9, live-confirmed: "refresh token was revoked").
  if (summary.runtime_credential_alert) {
    clauses.push({
      key: "runtime-credential-alert",
      text: `runtime CLI credential may be unhealthy — ${summary.runtime_credential_alert}`,
    });
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

/**
 * Overdue-patrol clause: last_patrol_at + 2x patrol_interval_minutes elapsed
 * with no newer patrol. Both fields are already on GET /api/qa/summary
 * (bu-hmdqz.9) -- entirely client-computable, no backend change needed.
 */
function buildOverduePatrolClause(summary: QaSummary): VerdictClause | null {
  if (!summary.last_patrol_at || !summary.patrol_interval_minutes) return null;

  const lastPatrolMs = new Date(summary.last_patrol_at).getTime();
  if (Number.isNaN(lastPatrolMs)) return null;

  const overdueThresholdMs = summary.patrol_interval_minutes * 60_000 * 2;
  if (Date.now() - lastPatrolMs < overdueThresholdMs) return null;

  return {
    key: "overdue-patrol",
    text: `patrol overdue — last patrol ${formatRelativeCompact(new Date(lastPatrolMs))}`,
  };
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
