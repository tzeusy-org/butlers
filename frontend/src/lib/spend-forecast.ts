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
}
