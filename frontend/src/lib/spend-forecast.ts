// ---------------------------------------------------------------------------
// Shared GET /api/spend/forecast response shape.
//
// Extracted from SpendPage.tsx so SpendVerdictOpener (components/costs/
// SpendVerdictOpener.tsx, bu-qvnce.9) can share the same type without a
// page-to-component import cycle.
// ---------------------------------------------------------------------------

export interface ForecastDay {
  date: string
  cost_usd: number
  projected: boolean
}

export interface ForecastData {
  days: ForecastDay[]
  projected_eom_usd: number
  days_in_month: number
  days_elapsed: number
  mtd_usd: number
  ceiling_usd: number | null
  projection_confidence: "low" | "normal"
  // True when pricing MTD from public.token_usage_ledger (the same source
  // check_monthly_ceiling gates spawns on) failed or the backend has no DB
  // pool wired -- mtd_usd/projected_eom_usd/ceiling_usd are then fabricated
  // zeros/null, not a genuine "$0 month" (bu-7o89u.1 degraded envelope).
  // Optional (rather than required) so older cached responses/fixtures that
  // predate this field don't fail a strict type check.
  ceiling_source_error?: boolean
  // Butlers dropped from the per-day fan-out powering the chart's solid
  // actuals series -- independent of ceiling_source_error.
  unavailable_butlers?: string[]
}
