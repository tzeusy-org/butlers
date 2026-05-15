import React from "react";
import type { OverviewRuntimeKpis } from "./model";
import { KpiStrip } from "./KpiStrip";

interface RuntimeSummaryKpiProps {
  kpis: OverviewRuntimeKpis;
  isLoading?: boolean;
  pendingApprovalsAvailable?: boolean;
}

export function RuntimeSummaryKpi({
  kpis,
  isLoading = false,
  pendingApprovalsAvailable = true,
}: RuntimeSummaryKpiProps) {
  const cells: React.ComponentProps<typeof KpiStrip>["cells"] = [
    {
      eyebrow: "Total butlers",
      value: isLoading ? "—" : kpis.totalButlers,
    },
    {
      eyebrow: "Healthy",
      value: isLoading ? "—" : kpis.healthyButlers,
    },
    {
      eyebrow: "Sessions · 24h",
      value: isLoading ? "—" : kpis.sessions24h,
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
