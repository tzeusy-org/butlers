// ---------------------------------------------------------------------------
// IssuesVerdictOpener — JARVIS pursuit move 9, slice 4 (bu-vyjoi)
// ---------------------------------------------------------------------------

import type { Issue } from "@/api/types";
import { DispatchVerdict, type VerdictClause } from "@/components/ui/dispatch-verdict";

export interface IssuesVerdictOpenerProps {
  issues: Issue[];
  isLoading: boolean;
  isError: boolean;
  activeWindow: string;
  /**
   * The scope this verdict is about, rendered by
   * {@link describeIssuesScope} (bu-6jv4m.3). The all-clear names it, because
   * "nothing here" inside a 7d window filtered to one group is not the same
   * claim as "the fleet is calm".
   */
  scopeLabel: string;
  showDismissed: boolean;
  sourcesDegraded: string[];
  auditGroupsTruncated: boolean;
}

function issueHref(window: string, severity: "critical" | "warning"): string {
  return `/issues?window=${encodeURIComponent(window)}&severity=${severity}`;
}

function plural(count: number, singular: string, pluralWord = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : pluralWord}`;
}

function buildClauses({
  issues,
  activeWindow,
  sourcesDegraded,
  auditGroupsTruncated,
}: Pick<
  IssuesVerdictOpenerProps,
  "issues" | "activeWindow" | "sourcesDegraded" | "auditGroupsTruncated"
>): VerdictClause[] {
  const clauses: VerdictClause[] = [];

  if (sourcesDegraded.length > 0) {
    clauses.push({
      key: "sources-degraded",
      text: `${sourcesDegraded.join(", ")} unavailable; issue feed may be incomplete`,
    });
  }
  if (auditGroupsTruncated) {
    clauses.push({
      key: "audit-groups-truncated",
      text: "audit issue groups are capped; older groups may be missing",
    });
  }

  const critical = issues.filter((issue) => issue.severity.toLowerCase() === "critical").length;
  const warning = issues.filter((issue) => issue.severity.toLowerCase() === "warning").length;

  if (critical > 0) {
    clauses.push({
      key: "critical-issues",
      text: `${plural(critical, "critical issue")} need review`,
      href: issueHref(activeWindow, "critical"),
    });
  }
  if (warning > 0) {
    clauses.push({
      key: "warning-issues",
      text: `${plural(warning, "warning group")} need review`,
      href: issueHref(activeWindow, "warning"),
    });
  }

  return clauses;
}

export function IssuesVerdictOpener({
  issues,
  isLoading,
  isError,
  activeWindow,
  scopeLabel,
  showDismissed,
  sourcesDegraded,
  auditGroupsTruncated,
}: IssuesVerdictOpenerProps) {
  return (
    <DispatchVerdict
      testId="issues"
      landmarkLabel="Issues verdict"
      sources={[{ label: "issue feed", isLoading, isError }]}
      clauses={buildClauses({ issues, activeWindow, sourcesDegraded, auditGroupsTruncated })}
      allClear={
        showDismissed
          ? `No acknowledged issues in ${scopeLabel}`
          : `No active issues in ${scopeLabel}`
      }
      className="border-b border-border/60 pb-3"
    />
  );
}
