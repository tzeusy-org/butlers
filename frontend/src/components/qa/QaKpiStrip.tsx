import type { QaActiveBreakdown, QaKpiBlock } from "@/api/types";
import { cn } from "@/lib/utils";

interface QaKpiStripProps {
  kpis: QaKpiBlock | null | undefined;
  active?: QaActiveBreakdown | null;
  className?: string;
}

interface KpiCell {
  id: string;
  label: string;
  value: string;
  sub: string;
}

function formatMttr(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.max(0, Math.floor(seconds))}s`;

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;

  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
}

function formatPercent(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${Math.round(value)}%`;
}

function formatActiveBreakdown(activeBreakdown: QaActiveBreakdown | null | undefined): string {
  const awaitingCi = activeBreakdown?.awaiting_ci ?? 0;
  const escalated = activeBreakdown?.escalated_open_cases ?? 0;
  return `${awaitingCi} awaiting CI · ${escalated} escalated`;
}

/**
 * Format a count delta sub-label: "+2 vs prior 24h" or "−2 vs prior 24h".
 * Returns null when prior value is not available.
 */
function formatCountDelta(
  current: number,
  prior: number | null | undefined,
  window: string,
): string | null {
  if (prior == null) return null;
  const delta = current - prior;
  const sign = delta >= 0 ? "+" : "−";
  return `${sign}${Math.abs(delta)} vs prior ${window}`;
}

/**
 * Format a duration delta sub-label: "+12m vs prior 24h" or "−12m vs prior 24h".
 * Returns null when either value is not available (no sample in that window).
 */
function formatMttrDelta(
  currentSeconds: number | null | undefined,
  priorSeconds: number | null | undefined,
  window: string,
): string | null {
  if (currentSeconds == null || priorSeconds == null) return null;
  const deltaSeconds = currentSeconds - priorSeconds;
  const sign = deltaSeconds >= 0 ? "+" : "−";
  return `${sign}${formatMttr(Math.abs(deltaSeconds))} vs ${window}`;
}

/**
 * Format a percentage-point delta sub-label: "+4pp vs prior week" or "−4pp vs prior week".
 * Returns null when prior value is not available (no sample in that window).
 */
function formatPctDelta(
  current: number,
  prior: number | null | undefined,
  window: string,
): string | null {
  if (prior == null) return null;
  const delta = Math.round(current) - Math.round(prior);
  const sign = delta >= 0 ? "+" : "−";
  return `${sign}${Math.abs(delta)}pp vs ${window}`;
}

export function QaKpiStrip({ kpis, active, className }: QaKpiStripProps) {
  const prsLandedDelta =
    kpis != null
      ? formatCountDelta(kpis.prs_landed_24h, kpis.prs_landed_prior_24h, "24h")
      : null;

  const mttrDelta =
    kpis != null
      ? formatMttrDelta(kpis.mttr_24h_seconds, kpis.mttr_prior_24h_seconds, "prior 24h")
      : null;

  const selfResolvedDelta =
    kpis != null
      ? formatPctDelta(kpis.self_resolved_7d_pct, kpis.self_resolved_prior_7d_pct, "prior week")
      : null;

  const cells: KpiCell[] = [
    {
      id: "prs-landed",
      label: "prs landed · 24h",
      value: kpis ? String(kpis.prs_landed_24h) : "—",
      sub: prsLandedDelta ?? "24h window",
    },
    {
      id: "mttr",
      label: "mttr · 24h",
      value: formatMttr(kpis?.mttr_24h_seconds),
      sub:
        kpis?.mttr_24h_seconds == null
          ? "no terminal cases in 24h"
          : (mttrDelta ?? "terminal cases in 24h"),
    },
    {
      id: "self-resolved",
      label: "self-resolved · 7d",
      value: formatPercent(kpis?.self_resolved_7d_pct),
      sub: selfResolvedDelta ?? "7d window",
    },
    {
      id: "active-cases",
      label: "active cases · now",
      value: kpis ? String(kpis.active_cases_now) : "—",
      sub: formatActiveBreakdown(active),
    },
  ];

  return (
    <div
      className={cn(
        "grid grid-cols-1 divide-y divide-border/60 sm:grid-cols-4 sm:divide-x sm:divide-y-0",
        className,
      )}
      role="group"
      aria-label="QA key performance indicators"
    >
      {cells.map((cell) => (
        <div key={cell.id} className="px-0 py-3 first:pt-0 last:pb-0 sm:px-4 sm:py-0 sm:first:pl-0 sm:last:pr-0">
          <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground tnum">
            {cell.label}
          </p>
          <p
            className="mb-1 font-sans text-[32px] font-medium leading-none tracking-[-0.03em] text-foreground tnum"
            data-testid={`qa-kpi-${cell.id}-value`}
          >
            {cell.value}
          </p>
          <p className="font-mono text-[10px] leading-none text-muted-foreground tnum">
            {cell.sub}
          </p>
        </div>
      ))}
    </div>
  );
}
