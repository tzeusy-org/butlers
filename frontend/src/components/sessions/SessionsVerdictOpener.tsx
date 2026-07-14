// ---------------------------------------------------------------------------
// SessionsVerdictOpener -- /sessions page opener (bu-y0v0c, JARVIS pursuit
// move 9, slice 3)
//
// Composes a window-scoped failure-clustering verdict from data SessionsPage
// already has reason to fetch: "N sessions failed in the last 24h, clustered
// on <butler|trigger>; nearest running session Ym elapsed" -- via the shared
// DispatchVerdict primitive. Both facts are exact and window-true (backed by
// GET /api/sessions/aggregate's by_butler/by_trigger_source and
// GET /api/sessions's own started_at-DESC ordering), never derived from a
// bounded/paged sample.
//
// The failure count/cluster is a real problem -> always a clause when
// present. The running-session note is informational (mirrors QA's
// last/next-patrol treatment) -- it only rides alongside the failure clause
// when there IS a failure to report (matching ApprovalsVerdictOpener's
// "nearest expiry" clause, itself only shown alongside "N waiting"); with no
// failures in the window it folds into the calm all-clear line instead.
// ---------------------------------------------------------------------------

import type { SessionAggregate, SessionSummary } from "@/api/index.ts";
import { DispatchVerdict, type VerdictClause } from "@/components/ui/dispatch-verdict";
import { elapsedText } from "@/lib/session-elapsed";

/** Lookback window for the failure-clustering verdict (matches the Overview
 * page's DEFAULT_RECENT_ISSUE_HOURS convention for "recent" windows). */
export const SESSIONS_VERDICT_WINDOW_HOURS = 24;

/** The dominant failure cluster: whichever axis (butler or trigger_source) is
 * more concentrated. Ties favor butler -- fewer, more directly actionable
 * groups than a raw trigger_source string. */
function dominantClusterLabel(agg: SessionAggregate): { label: string; href: string } | null {
  const topButler = agg.by_butler[0];
  const topTrigger = agg.by_trigger_source[0];
  if (!topButler && !topTrigger) return null;
  const useTrigger = topTrigger != null && (topButler == null || topTrigger.count > topButler.count);
  if (useTrigger && topTrigger) {
    return {
      label: topTrigger.trigger_source,
      href: `/sessions?status=failed&trigger=${encodeURIComponent(topTrigger.trigger_source)}`,
    };
  }
  if (topButler) {
    return {
      label: topButler.butler,
      href: `/sessions?status=failed&butler=${encodeURIComponent(topButler.butler)}`,
    };
  }
  return null;
}

function buildClauses(
  agg: SessionAggregate | undefined,
  runningSessions: SessionSummary[],
  sourcesDegraded: string[],
): VerdictClause[] {
  const clauses: VerdictClause[] = [];

  // Degraded fan-out (bu-tpudw.2): the failed-window aggregate fans out across
  // each butler's pool and drops any that error, naming them in
  // meta.sources_degraded rather than failing the request. When that list is
  // non-empty the failure count below UNDERCOUNTS, so the calm "No sessions
  // failed" verdict is a half-truth. Prepend a named clause (source-health
  // first, mirroring DispatchVerdict's own error-clause ordering) — its mere
  // presence suppresses the all-clear line so a downed pool never renders as a
  // clean window (CLAUDE.md degraded-envelope convention; mirrors
  // ApprovalsVerdictOpener).
  if (sourcesDegraded.length > 0) {
    clauses.push({
      key: "sources-degraded",
      text: `${sourcesDegraded.join(", ")} unreachable, so some failures may be missing`,
    });
  }

  if (agg && agg.total > 0) {
    const cluster = dominantClusterLabel(agg);
    const suffix = cluster ? `, clustered on ${cluster.label}` : "";
    clauses.push({
      key: "failed-cluster",
      text: `${agg.total} session${agg.total === 1 ? "" : "s"} failed in the last ${SESSIONS_VERDICT_WINDOW_HOURS}h${suffix}`,
      href: cluster?.href ?? "/sessions?status=failed",
    });

    const nearest = runningSessions[0];
    const elapsed = nearest ? elapsedText(nearest.started_at) : null;
    if (nearest && elapsed) {
      clauses.push({
        key: "nearest-running",
        text: `nearest running session ${elapsed}`,
        href: `/sessions/${nearest.id}`,
      });
    }
  }

  return clauses;
}

function buildAllClear(runningSessions: SessionSummary[]): string {
  const base = `No sessions failed in the last ${SESSIONS_VERDICT_WINDOW_HOURS}h`;
  const nearest = runningSessions[0];
  const elapsed = nearest ? elapsedText(nearest.started_at) : null;
  return nearest && elapsed ? `${base}; nearest running session ${elapsed}` : `${base}.`;
}

export interface SessionsVerdictOpenerProps {
  /** GET /api/sessions/aggregate?status=failed&since=<24h ago>&include_trigger_breakdown=true */
  failedAggregate: SessionAggregate | undefined;
  failedLoading: boolean;
  failedError: boolean;
  /** Butler pools dropped from the failed-window aggregate fan-out (its `meta.sources_degraded`). */
  failedSourcesDegraded?: string[];
  /** GET /api/sessions?status=running&limit=1 -- first row (started_at DESC) is the nearest-started running session. */
  runningSessions: SessionSummary[];
  runningLoading: boolean;
  runningError: boolean;
}

export function SessionsVerdictOpener({
  failedAggregate,
  failedLoading,
  failedError,
  failedSourcesDegraded = [],
  runningSessions,
  runningLoading,
  runningError,
}: SessionsVerdictOpenerProps) {
  const clauses = buildClauses(failedAggregate, runningSessions, failedSourcesDegraded);

  return (
    <DispatchVerdict
      testId="sessions"
      landmarkLabel="Sessions verdict"
      sources={[
        { label: "session failures", isLoading: failedLoading, isError: failedError },
        { label: "running sessions", isLoading: runningLoading, isError: runningError },
      ]}
      clauses={clauses}
      allClear={buildAllClear(runningSessions)}
    />
  );
}
