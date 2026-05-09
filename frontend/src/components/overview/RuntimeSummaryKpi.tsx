/**
 * RuntimeSummaryKpi -- 4-cell system runtime summary KPI card.
 *
 * Cells: total butlers / healthy butlers / sessions last 24h / pending approvals.
 *
 * Data sources (all existing hooks; no new endpoints):
 *   useButlers()          -> total, healthy, sessions_24h sum
 *   useApprovalMetrics()  -> total_pending (via useApprovalsPendingBadge)
 *
 * Styling: KpiStrip hairline grid — no per-cell card chrome. Tabular-nums on
 * all value slots. Loading shows '—'; zero-state renders '0'.
 *
 * bu-bm58r.1 -- Runtime summary KPI card
 */

import { useButlers } from "@/hooks/use-butlers";
import { useApprovalMetrics } from "@/hooks/use-approvals";
import { KpiStrip } from "./KpiStrip";

/**
 * Compose the 4-cell system runtime summary from existing hooks.
 *
 * Loading: '—' placeholder for each loading cell.
 * Zero-state: '0' (numeric zero rendered with tabular-nums).
 */
export function RuntimeSummaryKpi() {
  const { data: butlersResponse, isLoading: butlersLoading } = useButlers();
  const { data: approvalMetricsResponse, isLoading: approvalsLoading } = useApprovalMetrics();

  const butlers = butlersResponse?.data ?? [];
  const totalButlers = butlers.length;
  const healthyButlers = butlers.filter((b) => b.status === "ok").length;
  const sessions24h = butlers.reduce((sum, b) => sum + (b.sessions_24h ?? 0), 0);
  const pendingApprovals = approvalMetricsResponse?.data.total_pending ?? 0;

  const cells: [
    { eyebrow: string; value: string | number; delta?: string },
    { eyebrow: string; value: string | number; delta?: string },
    { eyebrow: string; value: string | number; delta?: string },
    { eyebrow: string; value: string | number; delta?: string },
  ] = [
    {
      eyebrow: "Total butlers",
      value: butlersLoading ? "—" : totalButlers,
    },
    {
      eyebrow: "Healthy",
      value: butlersLoading ? "—" : healthyButlers,
    },
    {
      eyebrow: "Sessions · 24h",
      value: butlersLoading ? "—" : sessions24h,
    },
    {
      eyebrow: "Pending approvals",
      value: approvalsLoading ? "—" : pendingApprovals,
    },
  ];

  return (
    <section aria-label="System runtime summary">
      <KpiStrip cells={cells} />
    </section>
  );
}
