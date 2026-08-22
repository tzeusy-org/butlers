/**
 * Exact Audit -> Issues evidence door for one failure row (bu-6jv4m.3).
 *
 * Replaces the unconditional `View in Issues ->` link that pointed at
 * `/issues?q=<first line of the error>`. That link asserted, without checking,
 * that (a) a group exists for this failure, (b) a first-line text guess
 * reproduces the backend's grouping normalization, and (c) the Issues page's
 * DEFAULT window contains it. When any of the three was wrong the user landed
 * on an empty Issues page, which reads as "nothing is wrong" -- an all-clear
 * manufactured by a failed lookup.
 *
 * This asks the server instead, and renders exactly what it gets back. There
 * are three outcomes and they are deliberately three different renderings:
 *
 *   found          -> a link carrying the exact `issue_key` AND the window the
 *                     group actually exists in.
 *   found: false   -> an EXPLICIT statement of absence, naming its scope. The
 *                     backend distinguishes "this row did not fail" from "it
 *                     failed but has no current group", so we do too.
 *   error          -> a degraded note. An unavailable lookup is NOT an absence
 *                     and must never be rendered as one.
 */

import { Link } from "react-router";

import type { AuditIssueGroupRef } from "@/api/types";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { useAuditIssueGroup } from "@/hooks/use-issues";

/** Human-readable name for a window token, for scoping the copy. */
function windowLabel(window: string): string {
  switch (window) {
    case "24h":
      return "the last 24h";
    case "7d":
      return "the last 7d";
    case "30d":
      return "the last 30d";
    case "all":
      return "all time";
    default:
      return `the last ${window}`;
  }
}

export interface AuditIssuesDoorProps {
  /** `public.audit_log` row id to resolve. */
  auditId: number;
  /**
   * Optional window to resolve in. Omitted, the server picks the narrowest
   * window that actually contains the row, so a historical failure widens to
   * "all" instead of resolving to a confident-looking nothing.
   */
  window?: string;
}

export function AuditIssuesDoor({ auditId, window }: AuditIssuesDoorProps) {
  // `enabled` is unconditionally true because this component is only mounted
  // for the expanded failure row -- the laziness lives at the call site.
  const query = useAuditIssueGroup(auditId, true, window);

  if (query.isError) {
    return (
      <SourceDegradedNote
        label="Issue group"
        detail="lookup unavailable, so this failure may or may not have an open issue"
        testId="audit-log-issues-degraded"
      />
    );
  }

  if (query.isPending || !query.data) {
    return (
      <p className="text-xs text-muted-foreground" data-testid="audit-log-issues-pending">
        Resolving issue group…
      </p>
    );
  }

  const ref: AuditIssueGroupRef = query.data.data;

  if (!ref.found || !ref.issues_href) {
    // Absence is stated, and stated with its scope. "not-a-failure" is a
    // different fact from "failed but no current group" and collapsing the two
    // would leave the user unable to tell an all-clear from an unmatched row.
    const message =
      ref.reason === "not-a-failure"
        ? "This entry did not fail, so it has no issue group."
        : `No current issue group for this failure in ${windowLabel(ref.window)}.`;
    return (
      <p className="text-xs text-muted-foreground" data-testid="audit-log-issues-absent">
        {message}
      </p>
    );
  }

  // No `?? 0` coercion: an absent count is a different fact from a count of
  // zero, and this component's whole job is to stop absence from being
  // silently rendered as a confident number (lint:query-coercion).
  const occurrences = typeof ref.occurrences === "number" ? ref.occurrences : null;
  return (
    <Link
      to={ref.issues_href}
      className="inline-flex text-xs font-medium text-[var(--red-text)] hover:underline"
      data-testid="audit-log-issues-link"
    >
      View in Issues → {occurrences !== null && occurrences > 0 ? `${occurrences}× in ` : ""}
      {windowLabel(ref.window)}
    </Link>
  );
}
