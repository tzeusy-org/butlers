/**
 * RuntimeSummaryKpi -- 4-cell system runtime summary KPI card.
 *
 * Cells: total butlers / healthy butlers / sessions last 24h / pending approvals.
 *
 * Data sources (all existing hooks; no new endpoints):
 *   useButlers()          -> total, healthy, sessions_24h sum (butler-type entries only)
 *   useApprovalMetrics()  -> total_pending
 *
 * Styling: KpiStrip hairline grid — no per-cell card chrome. Tabular-nums on
 * all value slots. Loading shows '—'; zero-state renders '0'.
 *
 * bu-bm58r.1 -- Runtime summary KPI card
 */

import React from "react";
import { useButlers } from "@/hooks/use-butlers";
import { useApprovalMetrics } from "@/hooks/use-approvals";
import { KpiStrip } from "./KpiStrip";

/**
 * Compose the 4-cell system runtime summary from existing hooks.
 *
 * Loading: all cells show '—' until both data sources are ready (prevents
 * partial-render layout shifts).
 * Zero-state: '0' (numeric zero rendered with tabular-nums).
 */
export function RuntimeSummaryKpi() {
  const { data: butlersResponse, isLoading: butlersLoading } = useButlers();
  const { data: approvalMetricsResponse, isLoading: approvalsLoading } = useApprovalMetrics();

  const isLoading = butlersLoading || approvalsLoading;

  const butlers = (butlersResponse?.data ?? []).filter((b) => b.type === "butler");
  const totalButlers = butlers.length;
  const healthyButlers = butlers.filter((b) => b.status === "ok" || b.status === "online").length;
  const sessions24h = butlers.reduce((sum, b) => sum + (b.sessions_24h ?? 0), 0);
  const pendingApprovals = approvalMetricsResponse?.data.total_pending ?? 0;

  const cells: React.ComponentProps<typeof KpiStrip>["cells"] = [
    {
      eyebrow: "Total butlers",
      value: isLoading ? "—" : totalButlers,
    },
    {
      eyebrow: "Healthy",
      value: isLoading ? "—" : healthyButlers,
    },
    {
      eyebrow: "Sessions · 24h",
      value: isLoading ? "—" : sessions24h,
    },
    {
      eyebrow: "Pending approvals",
      value: isLoading ? "—" : pendingApprovals,
    },
  ];

  return (
    <section aria-label="System runtime summary">
      <KpiStrip cells={cells} />
    </section>
  );
}
