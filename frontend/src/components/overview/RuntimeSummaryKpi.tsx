import React from "react";
import type { OverviewRuntimeKpis } from "./model";
import { KpiStrip } from "./KpiStrip";

interface RuntimeSummaryKpiProps {
  kpis: OverviewRuntimeKpis;
  isLoading?: boolean;
  /**
   * When true, the butlers query errored — Total/Healthy/Sessions reflect a
   * fallback empty list, not a genuine zero, so render '—' instead of 0.
   */
  isError?: boolean;
  pendingApprovalsAvailable?: boolean;
  /** False when the board's sessions aggregate is a partial sum. */
  sessionsAvailable?: boolean;
  /**
   * Closed 24-hour window backing the Sessions door (bu-27dxl.8.3) — the
   * SAME captured `since`/`until` instant the caller derived once for its
   * render, not a fresh `Date.now()` recomputed at click time.
   */
  sessionsSince?: string;
  sessionsUntil?: string;
}

/** Query-string door for the Sessions KPI cell, scoped to the one captured window. */
function sessionsHref(since: string | undefined, until: string | undefined): string {
  const params = new URLSearchParams();
  if (since) params.set("since", since);
  if (until) params.set("until", until);
  const qs = params.toString();
  return qs ? `/sessions?${qs}` : "/sessions";
}

export function RuntimeSummaryKpi({
  kpis,
  isLoading = false,
  isError = false,
  pendingApprovalsAvailable = true,
  sessionsAvailable = true,
  sessionsSince,
  sessionsUntil,
}: RuntimeSummaryKpiProps) {
  // Treat both the loading and error states as "no honest value yet": on error
  // the upstream butlers list is an empty fallback, so a literal 0 would lie.
  // Dashes never carry a door -- only a genuine (possibly zero) value does.
  const unavailable = isLoading || isError;
  const approvalsUnavailable = isLoading || !pendingApprovalsAvailable;
  const sessionsUnavailable = unavailable || !sessionsAvailable;
  const cells: React.ComponentProps<typeof KpiStrip>["cells"] = [
    {
      eyebrow: "Total butlers",
      value: unavailable ? "—" : kpis.totalButlers,
      href: unavailable ? undefined : "/butlers",
    },
    {
      eyebrow: "Healthy",
      value: unavailable ? "—" : kpis.healthyButlers,
      href: unavailable ? undefined : "/butlers",
      // Honest label: there is no healthy-only filter on /butlers -- this
      // door opens the same unfiltered fleet board as "Total butlers". No
      // em-dash (design-language.md non-negotiable #6): use a colon instead.
      ariaLabel: unavailable
        ? undefined
        : "Healthy butlers: opens the full unfiltered butler board",
    },
    {
      eyebrow: "Sessions · 24h",
      value: sessionsUnavailable ? "—" : kpis.sessions24h,
      unavailable: sessionsUnavailable,
      href: sessionsUnavailable ? undefined : sessionsHref(sessionsSince, sessionsUntil),
    },
    {
      eyebrow: "Pending approvals",
      value: approvalsUnavailable ? "—" : kpis.pendingApprovals,
      unavailable: approvalsUnavailable,
      href: approvalsUnavailable ? undefined : "/approvals",
    },
  ];

  return (
    <section aria-label="System runtime summary">
      <KpiStrip cells={cells} />
    </section>
  );
}
