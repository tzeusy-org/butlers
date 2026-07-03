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
