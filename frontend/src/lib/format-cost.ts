// ---------------------------------------------------------------------------
// Shared USD cost formatter [bu-86c4c.1]
//
// Multiple surfaces (Dashboard CostWidget, ButlerIndex, CostsPage,
// CostBreakdownTable) previously each rolled their own cost formatter with
// diverging precision — CostWidget/CostsPage clamped any nonzero sub-cent
// spend to the literal string "$0.00" (a real spend rendered as zero) while
// ButlerIndex showed 3-decimal precision for the same underlying by_butler
// data on the same screen. One shared formatter fixes both: never render a
// nonzero spend as "$0.00", and use one precision rule everywhere.
// ---------------------------------------------------------------------------

/**
 * Format a USD amount for display.
 *
 * - Exactly zero renders as "$0.00".
 * - A nonzero magnitude below one cent renders as "<$0.01" (never "$0.00" —
 *   that would misrepresent real spend as no spend at all), with a leading
 *   "-" preserved for negative amounts (e.g. refunds/credits).
 * - Everything else renders to 2 decimal places, sign preserved.
 */
export function formatCostUsd(amount: number): string {
  if (!Number.isFinite(amount) || amount === 0) return "$0.00";
  const sign = amount < 0 ? "-" : "";
  const magnitude = Math.abs(amount);
  if (magnitude < 0.01) return `${sign}<$0.01`;
  return `${sign}$${magnitude.toFixed(2)}`;
}

// ---------------------------------------------------------------------------
// Precise (4-decimal) variant [bu-sd0l7.3]
//
// Fine-grained per-tick/per-event cost cells (ingestion timeline) need more
// than 2 decimal places — a per-session cost is frequently a fraction of a
// cent, and 2dp would clamp most of them to "$0.00" the same way
// formatCostUsd's header above documents. This was a 3x-duplicated local
// `formatCost` in components/ingestion/timeline/{DispatchTicksCell,
// EventDrawer}.tsx and components/ingestion/TimelineTab.tsx before
// consolidation; kept as its own export (not a formatCostUsd parameter)
// because its null-vs-zero handling also differs ("—" for missing vs
// "$0.00" for exactly zero).
// ---------------------------------------------------------------------------

/**
 * Format a USD amount for fine-grained (4dp) display.
 *
 * - `null`/`undefined` renders as "—".
 * - Exactly zero renders as "$0.00".
 * - A nonzero magnitude below $0.001 renders as "<$0.001".
 * - Everything else renders to 4 decimal places.
 */
export function formatCostUsdPrecise(usd: number | undefined | null): string {
  if (usd === undefined || usd === null) return "—";
  if (usd === 0) return "$0.00";
  if (usd < 0.001) return "<$0.001";
  return `$${usd.toFixed(4)}`;
}
