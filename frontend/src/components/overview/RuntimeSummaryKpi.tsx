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
}

export function RuntimeSummaryKpi({
  kpis,
  isLoading = false,
  isError = false,
  pendingApprovalsAvailable = true,
}: RuntimeSummaryKpiProps) {
  // Treat both the loading and error states as "no honest value yet": on error
  // the upstream butlers list is an empty fallback, so a literal 0 would lie.
  const unavailable = isLoading || isError;
  const cells: React.ComponentProps<typeof KpiStrip>["cells"] = [
    {
      eyebrow: "Total butlers",
      value: unavailable ? "—" : kpis.totalButlers,
    },
    {
      eyebrow: "Healthy",
      value: unavailable ? "—" : kpis.healthyButlers,
    },
    {
      eyebrow: "Sessions · 24h",
      value: unavailable ? "—" : kpis.sessions24h,
    },
    {
      eyebrow: "Pending approvals",
      value: isLoading || !pendingApprovalsAvailable ? "—" : kpis.pendingApprovals,
    },
  ];

  return (
    <section aria-label="System runtime summary">
      <KpiStrip cells={cells} />
    </section>
  );
}
