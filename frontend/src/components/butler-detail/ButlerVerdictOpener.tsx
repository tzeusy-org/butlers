// ---------------------------------------------------------------------------
// ButlerVerdictOpener — JARVIS pursuit move 9, slice 4 (bu-vyjoi)
// ---------------------------------------------------------------------------

import type { ApprovalAction } from "@/api/types";
import type { ActivityVerb } from "@/hooks/use-butler-status-board";
import { DispatchVerdict, type VerdictClause } from "@/components/ui/dispatch-verdict";
import { formatCostUsd } from "@/lib/format-cost";

export interface ButlerVerdictOpenerProps {
  butlerName: string;
  activity: ActivityVerb | undefined;
  sessions24h: number | undefined;
  boardLoading: boolean;
  boardError: boolean;
  spendToday: number | undefined;
  spendLoading: boolean;
  spendError: boolean;
  spendSourcesDegraded: string[];
  pendingApprovals: ApprovalAction[];
  pendingTotal: number;
  approvalsLoading: boolean;
  approvalsError: boolean;
  failedSessions: number | undefined;
  failedSessionsLoading: boolean;
  failedSessionsError: boolean;
  approvalSourcesDegraded: string[];
  failureSourcesDegraded: string[];
}

function plural(count: number, singular: string, pluralWord = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : pluralWord}`;
}

function activityClause(activity: ActivityVerb | undefined): VerdictClause | null {
  if (!activity || activity === "idle" || activity === "running") return null;

  const label =
    activity === "offline"
      ? "butler is offline"
      : activity === "overdue"
        ? "butler is overdue"
        : activity === "quarantined"
          ? "butler is quarantined"
          : "butler status is unknown";
  return { key: `activity-${activity}`, text: label, href: "?tab=activity" };
}

function buildClauses({
  activity,
  pendingApprovals,
  pendingTotal,
  failedSessions,
  spendSourcesDegraded,
  approvalSourcesDegraded,
  failureSourcesDegraded,
  butlerName,
}: Pick<
  ButlerVerdictOpenerProps,
  | "activity"
  | "pendingApprovals"
  | "pendingTotal"
  | "failedSessions"
  | "spendSourcesDegraded"
  | "approvalSourcesDegraded"
  | "failureSourcesDegraded"
  | "butlerName"
>): VerdictClause[] {
  const clauses: VerdictClause[] = [];

  if (spendSourcesDegraded.length > 0) {
    clauses.push({
      key: "spend-sources-degraded",
      text: `${spendSourcesDegraded.join(", ")} unavailable; spend may be incomplete`,
    });
  }
  if (approvalSourcesDegraded.length > 0) {
    clauses.push({
      key: "approval-sources-degraded",
      text: `${approvalSourcesDegraded.join(", ")} unavailable; approvals may be incomplete`,
    });
  }
  if (failureSourcesDegraded.length > 0) {
    clauses.push({
      key: "failure-sources-degraded",
      text: `${failureSourcesDegraded.join(", ")} unavailable; failed sessions may be incomplete`,
    });
  }

  const status = activityClause(activity);
  if (status) clauses.push(status);

  if ((failedSessions ?? 0) > 0) {
    clauses.push({
      key: "failed-sessions",
      text: `${plural(failedSessions ?? 0, "session")} failed in the last 24h`,
      href: `/sessions?status=failed&butler=${encodeURIComponent(butlerName)}`,
    });
  }
  if (pendingTotal > 0) {
    const firstApproval = pendingApprovals[0];
    clauses.push({
      key: "pending-approvals",
      text: `${plural(pendingTotal, "approval")} waiting`,
      href: firstApproval ? `/approvals/${firstApproval.id}` : "?tab=approvals",
    });
  }

  return clauses;
}

export function ButlerVerdictOpener({
  butlerName,
  activity,
  sessions24h,
  boardLoading,
  boardError,
  spendToday,
  spendLoading,
  spendError,
  spendSourcesDegraded,
  pendingApprovals,
  pendingTotal,
  approvalsLoading,
  approvalsError,
  failedSessions,
  failedSessionsLoading,
  failedSessionsError,
  approvalSourcesDegraded,
  failureSourcesDegraded,
}: ButlerVerdictOpenerProps) {
  const allClear = `Nominal: ${plural(sessions24h ?? 0, "session")} in the last 24h, ${formatCostUsd(spendToday ?? 0)} spent today`;

  return (
    <DispatchVerdict
      testId="butler-detail"
      landmarkLabel={`Butler ${butlerName} verdict`}
      sources={[
        { label: "butler board", isLoading: boardLoading, isError: boardError },
        { label: "spend summary", isLoading: spendLoading, isError: spendError },
        { label: "pending approvals", isLoading: approvalsLoading, isError: approvalsError },
        {
          label: "failed sessions",
          isLoading: failedSessionsLoading,
          isError: failedSessionsError,
        },
      ]}
      clauses={buildClauses({
        activity,
        pendingApprovals,
        pendingTotal,
        failedSessions,
        spendSourcesDegraded,
        approvalSourcesDegraded,
        failureSourcesDegraded,
        butlerName,
      })}
      allClear={allClear}
      className="mb-5 border-b border-border/60 pb-3"
    />
  );
}
